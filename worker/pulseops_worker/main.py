"""Worker entry point: poll the queue, process one job at a time, repeat.

Run from the worker/ folder:   python -m pulseops_worker.main
"""

from __future__ import annotations

import logging
import os
import signal
import socket
import time
from pathlib import Path

import psycopg

from . import db
from .validation import validate_csv

log = logging.getLogger("pulseops.worker")
_running = True


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


def _upload_root() -> Path:
    return Path(os.environ.get("PULSEOPS_UPLOAD_DIR", "../uploads")).resolve()


def process_one(conn: psycopg.Connection, worker_id: str, upload_root: Path) -> bool:
    """Claim and process a single job. Returns False when the queue is empty."""
    job = db.claim_next_job(conn, worker_id)
    if job is None:
        return False

    ctx = f"job_id={job.id} tenant_id={job.tenant_id} correlation_id={job.correlation_id}"
    log.info("claimed %s attempt=%s/%s", ctx, job.attempt_count, job.max_attempts)
    started = time.monotonic()

    path = (upload_root / job.storage_key).resolve()
    if upload_root not in path.parents:
        status = db.record_unexpected_failure(
            conn, job, worker_id, "STORAGE_INVALID_KEY", "Storage key outside upload root", False
        )
        log.error("%s rejected unsafe storage key -> %s", ctx, status)
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
        log.error("%s file missing -> %s", ctx, status)
        return True

    try:
        result = validate_csv(path.read_bytes())
        status = db.save_results(conn, job, worker_id, result)
        log.info(
            "%s finished status=%s total=%s invalid=%s duration_ms=%d",
            ctx,
            status,
            result.total_rows,
            result.invalid_rows,
            (time.monotonic() - started) * 1000,
        )
    except db.LeaseLostError as exc:
        log.warning("%s %s; results discarded", ctx, exc)
    except Exception as exc:  # noqa: BLE001 - classify anything unexpected, never crash the loop
        log.exception("%s unexpected error", ctx)
        status = db.record_unexpected_failure(
            conn, job, worker_id, "PROCESSING_EXCEPTION", f"{type(exc).__name__}: {exc}", True
        )
        log.info("%s -> %s", ctx, status)
    return True


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    worker_id = f"{socket.gethostname()}-{os.getpid()}"
    upload_root = _upload_root()
    poll_seconds = float(os.environ.get("PULSEOPS_POLL_SECONDS", "2"))
    log.info("worker %s started, upload_root=%s", worker_id, upload_root)

    with psycopg.connect(_dsn(), autocommit=True) as conn:
        while _running:
            if not process_one(conn, worker_id, upload_root):
                time.sleep(poll_seconds)
    log.info("worker %s stopped", worker_id)


if __name__ == "__main__":
    main()
