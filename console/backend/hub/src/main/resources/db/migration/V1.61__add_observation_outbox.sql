-- Separate from business dispatch: exporting never authorizes model/tool re-execution.
CREATE TABLE oa_observation_outbox (
    id bigint NOT NULL AUTO_INCREMENT PRIMARY KEY,
    tenant_id varchar(80) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    environment varchar(8) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    run_id varchar(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    attempt_no int NOT NULL,
    revision bigint NOT NULL DEFAULT 1,
    acked_revision bigint NOT NULL DEFAULT 0,
    claim_id varchar(36) CHARACTER SET ascii COLLATE ascii_bin DEFAULT NULL,
    claim_owner varchar(128) CHARACTER SET ascii COLLATE ascii_bin DEFAULT NULL,
    claim_until timestamp(3) DEFAULT NULL,
    available_at timestamp(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    delivery_attempts bigint NOT NULL DEFAULT 0,
    last_error varchar(40) DEFAULT NULL,
    delivered_at timestamp(3) DEFAULT NULL,
    UNIQUE KEY uq_oa_observation_delivery(run_id,attempt_no),
    KEY ix_oa_observation_available(available_at,claim_until,id),
    KEY ix_oa_observation_environment(environment,acked_revision,revision),
    CONSTRAINT fk_oa_observation_attempt FOREIGN KEY(tenant_id,environment,run_id,attempt_no)
        REFERENCES oa_run_attempt(tenant_id,environment,run_id,attempt_no),
    CONSTRAINT ck_oa_observation_revision CHECK(revision>0 AND acked_revision>=0 AND acked_revision<=revision),
    CONSTRAINT ck_oa_observation_claim CHECK((claim_id IS NULL AND claim_owner IS NULL AND claim_until IS NULL)
        OR (claim_id IS NOT NULL AND claim_owner IS NOT NULL AND claim_until IS NOT NULL))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

INSERT INTO oa_observation_outbox(tenant_id,environment,run_id,attempt_no)
    SELECT DISTINCT s.tenant_id,s.environment,s.run_id,s.attempt_no
    FROM oa_run_step s JOIN oa_run_attempt a ON a.run_id=s.run_id AND a.attempt_no=s.attempt_no;
