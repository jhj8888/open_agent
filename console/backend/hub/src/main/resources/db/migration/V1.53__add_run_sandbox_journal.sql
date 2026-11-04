-- Reservations precede external creation. Neither worker reports nor lease expiry free quota.
ALTER TABLE oa_run_attempt ADD UNIQUE KEY uq_oa_attempt_scope_lease(tenant_id,environment,run_id,attempt_no,lease_id);

CREATE TABLE oa_run_sandbox (
    tenant_id varchar(80) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    environment varchar(8) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    run_id varchar(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    attempt_no int NOT NULL,
    lease_id varchar(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    isolation_profile varchar(16) NOT NULL,
    runtime_image varchar(512) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    sandbox_id varchar(36) CHARACTER SET ascii COLLATE ascii_bin DEFAULT NULL,
    phase varchar(24) NOT NULL DEFAULT 'reserved',
    reserved_at timestamp(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    creation_started_at timestamp(3) DEFAULT NULL,
    created_at timestamp(3) DEFAULT NULL,
    destruction_reported_at timestamp(3) DEFAULT NULL,
    released_at timestamp(3) DEFAULT NULL,
    verified_by varchar(128) CHARACTER SET ascii COLLATE ascii_bin DEFAULT NULL,
    verification_id varchar(36) CHARACTER SET ascii COLLATE ascii_bin DEFAULT NULL,
    PRIMARY KEY(lease_id),
    UNIQUE KEY uq_oa_run_sandbox_attempt(tenant_id,environment,run_id,attempt_no),
    UNIQUE KEY uq_oa_run_sandbox_id(sandbox_id),
    KEY ix_oa_run_sandbox_quota(tenant_id,environment,phase),
    KEY ix_oa_run_sandbox_reconcile(phase,reserved_at),
    CONSTRAINT fk_oa_run_sandbox_attempt FOREIGN KEY(tenant_id,environment,run_id,attempt_no,lease_id)
        REFERENCES oa_run_attempt(tenant_id,environment,run_id,attempt_no,lease_id),
    CONSTRAINT ck_oa_run_sandbox_profile CHECK(isolation_profile IN ('kata','gvisor-demo')
        AND (environment='test' OR isolation_profile='kata')),
    CONSTRAINT ck_oa_run_sandbox_phase CHECK(phase IN ('reserved','creating','created','cleanup_pending','released')),
    CONSTRAINT ck_oa_run_sandbox_creation CHECK((sandbox_id IS NULL AND created_at IS NULL)
        OR (sandbox_id IS NOT NULL AND created_at IS NOT NULL AND creation_started_at IS NOT NULL)),
    CONSTRAINT ck_oa_run_sandbox_release CHECK((phase='released' AND released_at IS NOT NULL
        AND verified_by IS NOT NULL AND verification_id IS NOT NULL) OR (phase<>'released'
        AND released_at IS NULL AND verified_by IS NULL AND verification_id IS NULL))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
