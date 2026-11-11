-- =========================================================
-- V2: manual retries + audit trail
-- =========================================================

-- How many times a person pressed "Retry" on this job (attempt_count is per round).
ALTER TABLE processing_jobs
    ADD COLUMN manual_retry_count INT NOT NULL DEFAULT 0;

-- Who did what, when. Significant actions only; never passwords or file contents.
CREATE TABLE audit_logs (
    id             BIGSERIAL    PRIMARY KEY,
    tenant_id      BIGINT       NOT NULL REFERENCES tenants(id),
    actor_type     VARCHAR(20)  NOT NULL CHECK (actor_type IN ('USER', 'SYSTEM')),
    actor_user_id  BIGINT       REFERENCES users(id),
    action         VARCHAR(50)  NOT NULL,
    entity_type    VARCHAR(30)  NOT NULL,
    entity_id      BIGINT       NOT NULL,
    correlation_id UUID,
    details        VARCHAR(1000),
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT now(),
    -- A USER action must name the user; a SYSTEM action (e.g. the worker) must not.
    CONSTRAINT ck_audit_actor CHECK ((actor_type = 'USER') = (actor_user_id IS NOT NULL))
);

-- The job page asks: "show the history of job X for tenant Y, oldest first".
CREATE INDEX idx_audit_entity ON audit_logs (tenant_id, entity_type, entity_id, created_at);
