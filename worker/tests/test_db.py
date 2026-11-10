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
        for table in ("validation_errors", "processed_transactions"):
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
