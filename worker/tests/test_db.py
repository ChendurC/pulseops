"""Database tests: run against a real PostgreSQL with the V1 schema.

Skipped unless PULSEOPS_TEST_DSN is set, e.g.
  PULSEOPS_TEST_DSN="host=localhost port=5432 dbname=pulseops user=pulseops password=..."
WARNING: uses a dedicated test tenant and deletes only that tenant's rows.
"""

import os
import uuid
from datetime import date

import psycopg
import pytest

from pulseops_worker import db
from pulseops_worker import main as worker_main
from pulseops_worker.validation import validate_csv

DSN = os.environ.get("PULSEOPS_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="PULSEOPS_TEST_DSN not set")
HEADER = "transaction_id,transaction_date,product_code,quantity,unit_price,country\n"
TODAY = date(2026, 11, 10)


@pytest.fixture()
def conn():
    with psycopg.connect(DSN, autocommit=True) as c:
        tid = c.execute(
            "INSERT INTO tenants (code, name, industry) VALUES (%s, 'Pytest Tenant', 'TEST') "
            "RETURNING id",
            (f"PYTEST-{uuid.uuid4().hex[:8]}",),
        ).fetchone()[0]
        c.tenant_id = tid
        yield c
        for table in ("audit_logs", "validation_errors", "processed_transactions"):
            c.execute(f"DELETE FROM {table} WHERE tenant_id = %s", (tid,))
        c.execute("DELETE FROM processing_jobs WHERE tenant_id = %s", (tid,))
        c.execute("DELETE FROM ingestions WHERE tenant_id = %s", (tid,))
        c.execute("DELETE FROM tenants WHERE id = %s", (tid,))


def queue_job(c, key="t/x.csv") -> int:
    ing = c.execute(
        "INSERT INTO ingestions (tenant_id, source, original_filename, storage_key, "
        "file_sha256, size_bytes) VALUES (%s,'UPLOAD','x.csv',%s,%s,1) RETURNING id",
        (c.tenant_id, key, "0" * 64),
    ).fetchone()[0]
    return c.execute(
        "INSERT INTO processing_jobs (tenant_id, ingestion_id, correlation_id) "
        "VALUES (%s,%s,%s) RETURNING id",
        (c.tenant_id, ing, uuid.uuid4()),
    ).fetchone()[0]


def job_row(c, job_id):
    return c.execute(
        "SELECT status, total_records, valid_records, invalid_records, error_category, "
        "attempt_count, worker_id FROM processing_jobs WHERE id = %s",
        (job_id,),
    ).fetchone()


def claim_own(c, worker, tenant_id=None):
    """Claim the next job and check it belongs to the test tenant."""
    job = db.claim_next_job(c, worker)
    assert job is not None and job.tenant_id == (tenant_id or c.tenant_id)
    return job


def test_claim_is_exclusive_between_two_workers(conn):
    j1, j2 = queue_job(conn), queue_job(conn)
    with psycopg.connect(DSN, autocommit=True) as other:
        a = claim_own(conn, "worker-a")
        b = claim_own(other, "worker-b", tenant_id=conn.tenant_id)
    assert {a.id, b.id} == {j1, j2}  # each worker got a different job
    assert a.id != b.id
    assert job_row(conn, a.id)[0] == "PROCESSING" and job_row(conn, a.id)[5] == 1


def test_save_results_completed_with_errors_and_exact_totals(conn):
    job_id = queue_job(conn)
    job = claim_own(conn, "w1")
    result = validate_csv(
        (HEADER + "A1,2026-10-01,P1,2,10.10,GB\nA2,2026-10-01,P1,0,1.00,GB\n").encode(), today=TODAY
    )
    assert db.save_results(conn, job, "w1", result) == "COMPLETED_WITH_ERRORS"
    status, total, valid, invalid, category, _, _ = job_row(conn, job_id)
    assert (status, total, valid, invalid, category) == (
        "COMPLETED_WITH_ERRORS",
        2,
        1,
        1,
        "DATA_VALIDATION",
    )
    total_sales = conn.execute(
        "SELECT sum(line_total) FROM processed_transactions WHERE job_id = %s", (job_id,)
    ).fetchone()[0]
    assert str(total_sales) == "20.20"


def test_rerun_of_same_job_does_not_duplicate_rows(conn):
    job_id = queue_job(conn)
    job = claim_own(conn, "w1")
    result = validate_csv((HEADER + "B1,2026-10-01,P1,1,1.00,GB\n").encode(), today=TODAY)
    db.save_results(conn, job, "w1", result)
    # Simulate a crash-recovery re-run of the same job by the same worker.
    conn.execute("UPDATE processing_jobs SET status='PROCESSING' WHERE id=%s", (job_id,))
    db.save_results(conn, job, "w1", result)
    count = conn.execute(
        "SELECT count(*) FROM processed_transactions WHERE job_id=%s", (job_id,)
    ).fetchone()[0]
    assert count == 1


def test_transaction_already_processed_by_earlier_job_is_reported_not_duplicated(conn):
    first = queue_job(conn)
    j = claim_own(conn, "w1")
    data = (HEADER + "C1,2026-10-01,P1,1,1.00,GB\n").encode()
    assert db.save_results(conn, j, "w1", validate_csv(data, today=TODAY)) == "COMPLETED"
    second = queue_job(conn)
    j2 = claim_own(conn, "w1")
    assert j2.id == second and first != second
    assert (
        db.save_results(conn, j2, "w1", validate_csv(data, today=TODAY)) == "COMPLETED_WITH_ERRORS"
    )
    err = conn.execute(
        "SELECT error_code FROM validation_errors WHERE job_id=%s", (second,)
    ).fetchone()[0]
    assert err == "ALREADY_PROCESSED"


def test_missing_columns_marks_job_failed(conn):
    job_id = queue_job(conn)
    job = claim_own(conn, "w1")
    result = validate_csv(b"transaction_id,quantity\nX,1\n", today=TODAY)
    assert db.save_results(conn, job, "w1", result) == "FAILED"
    assert job_row(conn, job_id)[4] == "SCHEMA_MISSING_COLUMNS"


def test_worker_that_lost_its_lease_cannot_overwrite(conn):
    job_id = queue_job(conn)
    job = claim_own(conn, "w-old")
    conn.execute("UPDATE processing_jobs SET worker_id='w-new' WHERE id=%s", (job_id,))
    result = validate_csv((HEADER + "D1,2026-10-01,P1,1,1.00,GB\n").encode(), today=TODAY)
    with pytest.raises(db.LeaseLostError):
        db.save_results(conn, job, "w-old", result)
    rows = conn.execute(
        "SELECT count(*) FROM processed_transactions WHERE job_id=%s", (job_id,)
    ).fetchone()[0]
    assert rows == 0  # the whole transaction was rolled back


def test_unexpected_error_requeues_then_fails_after_max_attempts(conn):
    job_id = queue_job(conn)
    conn.execute("UPDATE processing_jobs SET max_attempts=2 WHERE id=%s", (job_id,))
    job = claim_own(conn, "w1")
    assert (
        db.record_unexpected_failure(conn, job, "w1", "PROCESSING_EXCEPTION", "boom", True)
        == "QUEUED"
    )
    conn.execute("UPDATE processing_jobs SET available_at=now() WHERE id=%s", (job_id,))
    job = claim_own(conn, "w1")
    assert job.attempt_count == 2
    assert (
        db.record_unexpected_failure(conn, job, "w1", "PROCESSING_EXCEPTION", "boom", True)
        == "FAILED"
    )
    assert job_row(conn, job_id)[0] == "FAILED"


# ---------------------------------------------------------------- Day 3: crash recovery


def expire_lease(c, job_id):
    c.execute(
        "UPDATE processing_jobs SET lease_expires_at = now() - interval '1 second' WHERE id=%s",
        (job_id,),
    )


def audit_actions(c, job_id):
    return [
        r[0]
        for r in c.execute(
            "SELECT action FROM audit_logs WHERE entity_type='JOB' AND entity_id=%s ORDER BY id",
            (job_id,),
        )
    ]


def test_expired_lease_is_requeued_and_audited_as_system(conn):
    job_id = queue_job(conn)
    claim_own(conn, "crashed-worker")
    expire_lease(conn, job_id)  # simulate: worker died and its 5-minute promise ran out

    recovered = [r for r in db.recover_expired_leases(conn) if r.id == job_id]

    assert recovered and recovered[0].new_status == "QUEUED"
    status, *_rest, attempts, worker = job_row(conn, job_id)
    assert (status, attempts, worker) == ("QUEUED", 1, None)  # attempt 1 is remembered
    message = conn.execute(
        "SELECT error_message FROM processing_jobs WHERE id=%s", (job_id,)
    ).fetchone()[0]
    assert "crashed-worker" in message  # the evidence names the worker that died
    assert audit_actions(conn, job_id) == ["LEASE_EXPIRED_REQUEUED"]
    actor = conn.execute(
        "SELECT actor_type, actor_user_id FROM audit_logs WHERE entity_id=%s", (job_id,)
    ).fetchone()
    assert actor == ("SYSTEM", None)


def test_expired_lease_with_no_attempts_left_fails_the_job(conn):
    job_id = queue_job(conn)
    conn.execute("UPDATE processing_jobs SET max_attempts=1 WHERE id=%s", (job_id,))
    claim_own(conn, "crashed-worker")
    expire_lease(conn, job_id)
    db.recover_expired_leases(conn)
    assert job_row(conn, job_id)[0] == "FAILED"
    assert job_row(conn, job_id)[4] == "LEASE_EXPIRED"
    assert audit_actions(conn, job_id) == ["LEASE_EXPIRED_FAILED"]


def test_job_recovered_from_dead_worker_completes_once_without_duplicates(conn, tmp_path):
    key = f"tenant-{conn.tenant_id}/{uuid.uuid4()}.csv"
    (tmp_path / key).parent.mkdir(parents=True)
    (tmp_path / key).write_bytes((HEADER + "R1,2026-10-01,P1,2,5.00,GB\n").encode())
    job_id = queue_job(conn, key)

    dead = claim_own(conn, "dead-worker")  # claims, then "dies" without saving anything
    expire_lease(conn, job_id)
    db.recover_expired_leases(conn)

    assert worker_main.process_one(conn, "healthy-worker", tmp_path.resolve()) is True
    status, total, valid, *_ = job_row(conn, job_id)
    assert (status, total, valid) == ("COMPLETED", 1, 1)

    # The dead worker "wakes up" and tries to save late: it is fenced out.
    late = validate_csv((HEADER + "R1,2026-10-01,P1,2,5.00,GB\n").encode(), today=TODAY)
    conn.execute("UPDATE processing_jobs SET status='PROCESSING' WHERE id=%s", (job_id,))
    with pytest.raises(db.LeaseLostError):
        db.save_results(conn, dead, "dead-worker", late)
    rows = conn.execute(
        "SELECT count(*) FROM processed_transactions WHERE job_id=%s", (job_id,)
    ).fetchone()[0]
    assert rows == 1  # exactly once


def test_simulated_exception_requeues_job_with_backoff(conn, tmp_path):
    key = f"tenant-{conn.tenant_id}/{uuid.uuid4()}.csv"
    (tmp_path / key).parent.mkdir(parents=True)
    (tmp_path / key).write_bytes((HEADER + "S1,2026-10-01,P1,1,1.00,GB\n").encode())
    job_id = queue_job(conn, key)

    faults = worker_main.Faults(raise_error=True)
    assert worker_main.process_one(conn, "w1", tmp_path.resolve(), faults=faults) is True

    row = conn.execute(
        "SELECT status, error_category, available_at > now() FROM processing_jobs WHERE id=%s",
        (job_id,),
    ).fetchone()
    assert row == ("QUEUED", "PROCESSING_EXCEPTION", True)  # back in queue, delayed


def test_missing_upload_file_fails_without_retry(conn, tmp_path):
    job_id = queue_job(conn, f"tenant-{conn.tenant_id}/does-not-exist.csv")
    worker_main.process_one(conn, "w1", tmp_path.resolve())
    assert job_row(conn, job_id)[0] == "FAILED"
    assert job_row(conn, job_id)[4] == "STORAGE_FILE_MISSING"
