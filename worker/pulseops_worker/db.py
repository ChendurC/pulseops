"""All SQL used by the worker. Every write for a job happens in ONE transaction."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

import psycopg

from .validation import RowError, ValidationResult

DEFAULT_LEASE_SECONDS = 300


@dataclass(frozen=True)
class ClaimedJob:
    id: int
    tenant_id: int
    correlation_id: UUID
    attempt_count: int
    max_attempts: int
    storage_key: str


class LeaseLostError(Exception):
    """Another worker took over this job; our results must not be saved."""


# FOR UPDATE SKIP LOCKED: if two workers run this at the same moment, each one
# locks a DIFFERENT queued row instead of both grabbing the same job.
CLAIM_SQL = """
UPDATE processing_jobs j
   SET status           = 'PROCESSING',
       attempt_count    = j.attempt_count + 1,
       started_at       = now(),
       lease_expires_at = now() + make_interval(secs => %(lease)s),
       worker_id        = %(worker_id)s
 WHERE j.id = (
        SELECT id FROM processing_jobs
         WHERE status = 'QUEUED' AND available_at <= now()
         ORDER BY available_at, id
         FOR UPDATE SKIP LOCKED
         LIMIT 1)
RETURNING j.id, j.tenant_id, j.correlation_id, j.attempt_count, j.max_attempts,
          (SELECT storage_key FROM ingestions i WHERE i.id = j.ingestion_id)
"""


def claim_next_job(
    conn: psycopg.Connection, worker_id: str, lease_seconds: int = DEFAULT_LEASE_SECONDS
) -> ClaimedJob | None:
    with conn.transaction():
        row = conn.execute(CLAIM_SQL, {"lease": lease_seconds, "worker_id": worker_id}).fetchone()
    return ClaimedJob(*row) if row else None


def _insert_errors(cur: psycopg.Cursor, job: ClaimedJob, errors: list[RowError]) -> None:
    if not errors:
        return
    cur.executemany(
        """INSERT INTO validation_errors
               (tenant_id, job_id, line_number, column_name, error_code, message, raw_value)
           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
        [
            (
                job.tenant_id,
                job.id,
                e.line_number,
                e.column_name,
                e.error_code,
                e.message[:500],
                e.raw_value,
            )
            for e in errors
        ],
    )


def _finish(cur: psycopg.Cursor, job: ClaimedJob, worker_id: str, **fields) -> None:
    """Final status update, fenced: only succeeds if we still own the lease."""
    cur.execute(
        """UPDATE processing_jobs
              SET status = %(status)s, total_records = %(total)s, valid_records = %(valid)s,
                  invalid_records = %(invalid)s, error_category = %(category)s,
                  error_message = %(message)s, finished_at = now(), lease_expires_at = NULL
            WHERE id = %(id)s AND status = 'PROCESSING' AND worker_id = %(worker_id)s""",
        {"id": job.id, "worker_id": worker_id, **fields},
    )
    if cur.rowcount != 1:
        raise LeaseLostError(f"job {job.id} is no longer owned by {worker_id}")


def save_results(
    conn: psycopg.Connection, job: ClaimedJob, worker_id: str, result: ValidationResult
) -> str:
    """Store valid rows, errors and the final status atomically. Returns the final status."""
    with conn.transaction(), conn.cursor() as cur:
        # Idempotent re-run: remove anything a previous (crashed) attempt left behind.
        cur.execute("DELETE FROM processed_transactions WHERE job_id = %s", (job.id,))
        cur.execute("DELETE FROM validation_errors WHERE job_id = %s", (job.id,))

        if result.file_error:
            _insert_errors(cur, job, [result.file_error])
            _finish(
                cur,
                job,
                worker_id,
                status="FAILED",
                total=result.total_rows,
                valid=0,
                invalid=result.total_rows,
                category=result.file_error.error_code,
                message=result.file_error.message,
            )
            return "FAILED"

        errors = list(result.errors)
        invalid = result.invalid_rows
        inserted = 0

        if result.valid_rows:
            # ON CONFLICT DO NOTHING + RETURNING tells us exactly which rows were new.
            # A transaction_id already stored by an EARLIER job is reported, not duplicated.
            cur.executemany(
                """INSERT INTO processed_transactions
                       (tenant_id, job_id, transaction_id, transaction_date, product_code,
                        quantity, unit_price, line_total, country)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (tenant_id, transaction_id) DO NOTHING
                   RETURNING transaction_id""",
                [
                    (
                        job.tenant_id,
                        job.id,
                        r.transaction_id,
                        r.transaction_date,
                        r.product_code,
                        r.quantity,
                        r.unit_price,
                        r.line_total,
                        r.country,
                    )
                    for r in result.valid_rows
                ],
                returning=True,
            )
            stored: set[str] = set()
            while True:
                row = cur.fetchone()
                if row:
                    stored.add(row[0])
                if not cur.nextset():
                    break
            inserted = len(stored)
            for r in result.valid_rows:
                if r.transaction_id not in stored:
                    invalid += 1
                    errors.append(
                        RowError(
                            r.line_number,
                            "transaction_id",
                            "ALREADY_PROCESSED",
                            "transaction_id was already processed in an earlier submission",
                            r.transaction_id,
                        )
                    )

        _insert_errors(cur, job, errors)
        status = "COMPLETED" if invalid == 0 else "COMPLETED_WITH_ERRORS"
        _finish(
            cur,
            job,
            worker_id,
            status=status,
            total=result.total_rows,
            valid=inserted,
            invalid=invalid,
            category=None if invalid == 0 else "DATA_VALIDATION",
            message=None if invalid == 0 else f"{invalid} row(s) rejected",
        )
        return status


def record_unexpected_failure(
    conn: psycopg.Connection,
    job: ClaimedJob,
    worker_id: str,
    category: str,
    message: str,
    retryable: bool,
) -> str:
    """Either put the job back in the queue with a delay, or fail it permanently."""
    with conn.transaction(), conn.cursor() as cur:
        if retryable and job.attempt_count < job.max_attempts:
            delay = 10 * job.attempt_count  # simple linear back-off: 10s, 20s, ...
            cur.execute(
                """UPDATE processing_jobs
                      SET status = 'QUEUED', available_at = now() + make_interval(secs => %s),
                          lease_expires_at = NULL, worker_id = NULL,
                          error_category = %s, error_message = %s
                    WHERE id = %s AND status = 'PROCESSING' AND worker_id = %s""",
                (delay, category, message[:1000], job.id, worker_id),
            )
            return "QUEUED"
        _finish(
            cur,
            job,
            worker_id,
            status="FAILED",
            total=None,
            valid=None,
            invalid=None,
            category=category,
            message=message[:1000],
        )
        return "FAILED"


# A job whose lease ran out was abandoned by a worker that crashed, was killed or froze.
# Put it back in the queue (or fail it if it has used all its attempts).
# SET expressions read the OLD row values, so the message can name the dead worker.
RECOVER_SQL = """
UPDATE processing_jobs
   SET status           = CASE WHEN attempt_count < max_attempts THEN 'QUEUED' ELSE 'FAILED' END,
       finished_at      = CASE WHEN attempt_count < max_attempts THEN NULL ELSE now() END,
       available_at     = now(),
       error_category   = 'LEASE_EXPIRED',
       error_message    = 'Worker ' || coalesce(worker_id, '?')
                          || ' stopped responding before finishing; lease expired',
       worker_id        = NULL,
       lease_expires_at = NULL
 WHERE status = 'PROCESSING' AND lease_expires_at < now()
RETURNING id, tenant_id, correlation_id, status, error_message
"""


@dataclass(frozen=True)
class RecoveredJob:
    id: int
    tenant_id: int
    correlation_id: UUID
    new_status: str
    message: str


def recover_expired_leases(conn: psycopg.Connection) -> list[RecoveredJob]:
    """Re-queue or fail abandoned jobs and write a SYSTEM audit entry for each."""
    with conn.transaction(), conn.cursor() as cur:
        rows = [RecoveredJob(*r) for r in cur.execute(RECOVER_SQL).fetchall()]
        if rows:
            cur.executemany(
                """INSERT INTO audit_logs (tenant_id, actor_type, action, entity_type, entity_id,
                                           correlation_id, details)
                   VALUES (%s, 'SYSTEM', %s, 'JOB', %s, %s, %s)""",
                [
                    (
                        r.tenant_id,
                        "LEASE_EXPIRED_REQUEUED"
                        if r.new_status == "QUEUED"
                        else "LEASE_EXPIRED_FAILED",
                        r.id,
                        r.correlation_id,
                        r.message[:1000],
                    )
                    for r in rows
                ],
            )
    return rows
