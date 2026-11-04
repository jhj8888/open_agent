-- Trusted supervisors are separate identities from executors. No input or model keys here.
CREATE TABLE oa_run_supervision (
    lease_id varchar(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL PRIMARY KEY,
    supervisor_id varchar(128) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    boot_id varchar(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    unit_name varchar(80) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    invocation_id char(32) CHARACTER SET ascii COLLATE ascii_bin DEFAULT NULL,
    phase varchar(16) NOT NULL DEFAULT 'prepared',
    created_at timestamp(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    launch_started_at timestamp(3) DEFAULT NULL,
    observed_at timestamp(3) DEFAULT NULL,
    released_at timestamp(3) DEFAULT NULL,
    proof_id varchar(36) CHARACTER SET ascii COLLATE ascii_bin DEFAULT NULL,
    proof_json json DEFAULT NULL,
    UNIQUE KEY uq_oa_supervision_unit (supervisor_id,unit_name),
    UNIQUE KEY uq_oa_supervision_proof (proof_id),
    KEY ix_oa_supervision_pending (supervisor_id,phase,created_at,lease_id),
    CONSTRAINT fk_oa_supervision_attempt FOREIGN KEY (lease_id) REFERENCES oa_run_attempt(lease_id),
    CONSTRAINT ck_oa_supervision_phase CHECK (phase IN ('prepared','launching','observed','released')),
    CONSTRAINT ck_oa_supervision_launch CHECK (
        (phase='prepared' AND launch_started_at IS NULL AND invocation_id IS NULL AND observed_at IS NULL)
        OR (phase='launching' AND launch_started_at IS NOT NULL AND invocation_id IS NULL AND observed_at IS NULL)
        OR (phase='observed' AND launch_started_at IS NOT NULL AND invocation_id IS NOT NULL AND observed_at IS NOT NULL)
        OR phase='released'),
    CONSTRAINT ck_oa_supervision_release CHECK (
        (phase='released' AND released_at IS NOT NULL AND proof_id IS NOT NULL AND proof_json IS NOT NULL)
        OR (phase<>'released' AND released_at IS NULL AND proof_id IS NULL AND proof_json IS NULL))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
