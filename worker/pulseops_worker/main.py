"""Worker entry point: recover abandoned jobs, claim one job, process it, repeat.

Run from the worker/ folder:   python -m pulseops_worker.main
"""

from __future__ import annotations

import logging
import os
import signal
import socket
import time
from dataclasses import dataclass
from pathlib import Path

import psycopg

from . import db
from .logs import configure_logging
from .validation import validate_csv

log = logging.getLogger("pulseops.worker")
_running = True


@dataclass(frozen=True)
class Faults:
    """Development-only fault injection, used to demonstrate failure handling.

    Only honoured when SPRING_PROFILES_ACTIVE=dev, so it can never trigger in production.
    """

    slow_seconds: float = 0.0  # pause after claiming, so the worker can be killed mid-job
    raise_error: bool = False  # simulate an unexpected crash inside processing

    @staticmethod
    def from_env() -> Faults:
        if os.environ.get("SPRING_PROFILES_ACTIVE") != "dev":
            return Faults()
        return Faults(
            slow_seconds=float(os.environ.get("PULSEOPS_FAULT_SLOW_SECONDS", "0")),
            raise_error=os.environ.get("PULSEOPS_FAULT_EXCEPTION", "") == "1",
        )

    @property
    def active(self) -> bool:
        return self.slow_seconds > 0 or self.raise_error


def _stop(signum, _frame) -> None:
    global _running
    log.info("signal %s received, finishing current job then stopping", signum)
    _running = False


def _dsn() -> str:
    password = os.environ.get("POSTGRES_PASSWORD")
    if not password:
        raise SystemExit("POSTGRES_PASSWORD is not set. Run: set -a && source ../.env && set +a")
    return (
        f"host={os.environ.get('PULSEOPS_DB_HOST', 'localhost')} "
        f"port={os.environ.get('PULSEOPS_DB_PORT', '5432')} "
        f"dbname={os.environ.get('PULSEOPS_DB_NAME', 'pulseops')} "
        f"user={os.environ.get('PULSEOPS_DB_USER', 'pulseops')} password={password}"
    )


def recover(conn: psycopg.Connection) -> int:
    recovered = db.recover_expired_leases(conn)
    for r in recovered:
        log.warning(
            "lease expired, job %s",
            "re-queued" if r.new_status == "QUEUED" else "failed (no attempts left)",
            extra={
                "job_id": r.id,
                "tenant_id": r.tenant_id,
                "correlation_id": r.correlation_id,
                "status": r.new_status,
                "error_category": "LEASE_EXPIRED",
            },
        )
    return len(recovered)


def process_one(
    conn: psycopg.Connection,
    worker_id: str,
    upload_root: Path,
    lease_seconds: int = db.DEFAULT_LEASE_SECONDS,
    faults: Faults | None = None,
) -> bool:
    """Claim and process a single job. Returns False when the queue is empty."""
    faults = faults or Faults()
    job = db.claim_next_job(conn, worker_id, lease_seconds)
    if job is None:
        return False

    ctx = {
        "job_id": job.id,
        "tenant_id": job.tenant_id,
        "correlation_id": job.correlation_id,
        "worker_id": worker_id,
    }
    log.info("job claimed", extra={**ctx, "attempt": f"{job.attempt_count}/{job.max_attempts}"})
    started = time.monotonic()

    path = (upload_root / job.storage_key).resolve()
    if upload_root not in path.parents:
        status = db.record_unexpected_failure(
            conn, job, worker_id, "STORAGE_INVALID_KEY", "Storage key outside upload root", False
        )
        log.error("unsafe storage key rejected", extra={**ctx, "status": status})
        return True
    if not path.is_file():
        status = db.record_unexpected_failure(
            conn,
            job,
            worker_id,
            "STORAGE_FILE_MISSING",
            f"Uploaded file not found: {job.storage_key}",
            False,
        )
        log.error("uploaded file missing", extra={**ctx, "status": status})
        return True

    try:
        if faults.slow_seconds:
            log.warning("FAULT INJECTION: sleeping %.0fs mid-job", faults.slow_seconds, extra=ctx)
            time.sleep(faults.slow_seconds)
        if faults.raise_error:
            raise RuntimeError("Simulated worker failure (PULSEOPS_FAULT_EXCEPTION=1)")

        result = validate_csv(path.read_bytes())
        status = db.save_results(conn, job, worker_id, result)
        log.info(
            "job finished",
            extra={
                **ctx,
                "status": status,
                "total": result.total_rows,
                "invalid": result.invalid_rows,
                "duration_ms": int((time.monotonic() - started) * 1000),
            },
        )
    except db.LeaseLostError as exc:
        log.warning("lease lost, results discarded: %s", exc, extra=ctx)
    except Exception as exc:  # noqa: BLE001 - classify anything unexpected, never crash the loop
        log.exception("unexpected error while processing", extra=ctx)
        status = db.record_unexpected_failure(
            conn, job, worker_id, "PROCESSING_EXCEPTION", f"{type(exc).__name__}: {exc}", True
        )
        log.info("failure recorded", extra={**ctx, "status": status})
    return True


def main() -> None:
    log_file = configure_logging(Path(os.environ.get("PULSEOPS_LOG_DIR", "../logs")).resolve())
    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    worker_id = f"{socket.gethostname()}-{os.getpid()}"
    upload_root = Path(os.environ.get("PULSEOPS_UPLOAD_DIR", "../uploads")).resolve()
    poll_seconds = float(os.environ.get("PULSEOPS_POLL_SECONDS", "2"))
    lease_seconds = int(os.environ.get("PULSEOPS_LEASE_SECONDS", str(db.DEFAULT_LEASE_SECONDS)))
    faults = Faults.from_env()

    log.info(
        "worker started upload_root=%s lease=%ss json_log=%s",
        upload_root,
        lease_seconds,
        log_file,
        extra={"worker_id": worker_id},
    )
    if faults.active:
        log.warning("FAULT INJECTION ENABLED (dev only): %s", faults)

    with psycopg.connect(_dsn(), autocommit=True) as conn:
        while _running:
            recover(conn)
            if not process_one(conn, worker_id, upload_root, lease_seconds, faults):
                time.sleep(poll_seconds)
    log.info("worker stopped", extra={"worker_id": worker_id})


if __name__ == "__main__":
    main()
