-- =========================================================
-- V1: core multi-tenant schema for PulseOps
-- =========================================================

-- A tenant = one customer company. Every business row belongs to one.
CREATE TABLE tenants (
    id          BIGSERIAL    PRIMARY KEY,
    code        VARCHAR(50)  NOT NULL UNIQUE,
    name        VARCHAR(200) NOT NULL,
    industry    VARCHAR(50)  NOT NULL,
    active      BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- Users always belong to exactly one tenant.
CREATE TABLE users (
    id             BIGSERIAL    PRIMARY KEY,
    tenant_id      BIGINT       NOT NULL REFERENCES tenants(id),
    username       VARCHAR(100) NOT NULL UNIQUE,
    password_hash  VARCHAR(100) NOT NULL,
    role           VARCHAR(30)  NOT NULL
                   CHECK (role IN ('TENANT_ADMIN','ANALYST','SUPPORT_ENGINEER')),
    enabled        BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX idx_users_tenant ON users (tenant_id);

-- One row per file received (web upload or API).
CREATE TABLE ingestions (
    id                BIGSERIAL    PRIMARY KEY,
    tenant_id         BIGINT       NOT NULL REFERENCES tenants(id),
    source            VARCHAR(10)  NOT NULL CHECK (source IN ('UPLOAD','API')),
    original_filename VARCHAR(255) NOT NULL,
    storage_key       VARCHAR(500) NOT NULL,
    file_sha256       CHAR(64)     NOT NULL,
    size_bytes        BIGINT       NOT NULL CHECK (size_bytes >= 0),
    idempotency_key   VARCHAR(100),
    created_by        BIGINT       REFERENCES users(id),
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT uq_ingestions_tenant_idem UNIQUE (tenant_id, idempotency_key)
);

-- The job queue. The Python worker claims rows from here.
CREATE TABLE processing_jobs (
    id               BIGSERIAL   PRIMARY KEY,
    tenant_id        BIGINT      NOT NULL REFERENCES tenants(id),
    ingestion_id     BIGINT      NOT NULL UNIQUE REFERENCES ingestions(id),
    status           VARCHAR(30) NOT NULL DEFAULT 'QUEUED'
                     CHECK (status IN ('QUEUED','PROCESSING','COMPLETED',
                                       'COMPLETED_WITH_ERRORS','FAILED')),
    correlation_id   UUID        NOT NULL,
    attempt_count    INT         NOT NULL DEFAULT 0,
    max_attempts     INT         NOT NULL DEFAULT 3,
    available_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    lease_expires_at TIMESTAMPTZ,
    worker_id        VARCHAR(100),
    total_records    INT,
    valid_records    INT,
    invalid_records  INT,
    error_category   VARCHAR(50),
    error_message    TEXT,
    submitted_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at       TIMESTAMPTZ,
    finished_at      TIMESTAMPTZ
);
CREATE INDEX idx_jobs_claim  ON processing_jobs (available_at)     WHERE status = 'QUEUED';
CREATE INDEX idx_jobs_lease  ON processing_jobs (lease_expires_at) WHERE status = 'PROCESSING';
CREATE INDEX idx_jobs_tenant ON processing_jobs (tenant_id, submitted_at DESC);

-- Clean, validated rows produced by the worker.
CREATE TABLE processed_transactions (
    id               BIGSERIAL     PRIMARY KEY,
    tenant_id        BIGINT        NOT NULL REFERENCES tenants(id),
    job_id           BIGINT        NOT NULL REFERENCES processing_jobs(id),
    transaction_id   VARCHAR(100)  NOT NULL,
    transaction_date DATE          NOT NULL,
    product_code     VARCHAR(50)   NOT NULL,
    quantity         INT           NOT NULL CHECK (quantity > 0),
    unit_price       NUMERIC(12,2) NOT NULL CHECK (unit_price >= 0),
    line_total       NUMERIC(14,2) NOT NULL,
    country          CHAR(2)       NOT NULL,
    created_at       TIMESTAMPTZ   NOT NULL DEFAULT now(),
    CONSTRAINT uq_txn_tenant_txnid UNIQUE (tenant_id, transaction_id)
);
CREATE INDEX idx_txn_job         ON processed_transactions (job_id);
CREATE INDEX idx_txn_tenant_date ON processed_transactions (tenant_id, transaction_date);

-- Every rejected row, with the reason, for the job details screen.
CREATE TABLE validation_errors (
    id           BIGSERIAL    PRIMARY KEY,
    tenant_id    BIGINT       NOT NULL REFERENCES tenants(id),
    job_id       BIGINT       NOT NULL REFERENCES processing_jobs(id),
    line_number  INT,
    column_name  VARCHAR(50),
    error_code   VARCHAR(50)  NOT NULL,
    message      VARCHAR(500) NOT NULL,
    raw_value    VARCHAR(255),
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX idx_valerr_job ON validation_errors (job_id);
