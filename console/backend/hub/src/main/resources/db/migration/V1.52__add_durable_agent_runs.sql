-- Additive control-plane records. Deployment IDs denote immutable execution snapshots.
-- Existing workflow, Kafka, Celery and Redis data are not changed.
CREATE TABLE oa_agent_deployment (
    tenant_id varchar(80) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    environment varchar(8) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    deployment_id varchar(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    display_name varchar(255) NOT NULL,
    executor_kind varchar(16) NOT NULL,
    agent_version varchar(128) NOT NULL,
    model_connection_id varchar(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    model_version bigint NOT NULL,
    snapshot_json longtext NOT NULL,
    snapshot_sha256 char(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    state varchar(16) NOT NULL,
    created_by varchar(128) COLLATE utf8mb4_bin NOT NULL,
    created_at timestamp(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    PRIMARY KEY(tenant_id,environment,deployment_id),
    UNIQUE KEY uq_oa_deployment_id(deployment_id),
    CONSTRAINT fk_oa_deployment_model FOREIGN KEY(tenant_id,environment,model_connection_id,model_version)
        REFERENCES oa_model_connection_version(tenant_id,environment,connection_id,version),
    CONSTRAINT ck_oa_deployment_kind CHECK(executor_kind IN ('deepagents','workflow')),
    CONSTRAINT ck_oa_deployment_state CHECK(state IN ('test','published','disabled')),
    CONSTRAINT ck_oa_deployment_environment CHECK((environment='test' AND state IN ('test','disabled'))
        OR (environment='prod' AND state IN ('published','disabled'))),
    CONSTRAINT ck_oa_deployment_snapshot CHECK(JSON_VALID(snapshot_json))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE oa_agent_deployment_skill (
    tenant_id varchar(80) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    environment varchar(8) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    deployment_id varchar(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    release_id varchar(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    manifest_sha256 char(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    PRIMARY KEY(tenant_id,environment,deployment_id,release_id),
    CONSTRAINT fk_oa_deployment_skill_parent FOREIGN KEY(tenant_id,environment,deployment_id)
        REFERENCES oa_agent_deployment(tenant_id,environment,deployment_id),
    CONSTRAINT fk_oa_deployment_skill_grant FOREIGN KEY(tenant_id,environment,release_id)
        REFERENCES oa_skill_release_environment(tenant_id,environment,release_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE oa_agent_run (
    tenant_id varchar(80) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    environment varchar(8) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    run_id varchar(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    deployment_id varchar(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    actor_id varchar(128) COLLATE utf8mb4_bin NOT NULL,
    idempotency_key varchar(128) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    request_sha256 char(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    input_json longtext NOT NULL,
    snapshot_json longtext NOT NULL,
    snapshot_sha256 char(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    state varchar(24) NOT NULL DEFAULT 'queued',
    revision bigint NOT NULL DEFAULT 0,
    last_event_seq bigint NOT NULL DEFAULT 0,
    event_bytes bigint NOT NULL DEFAULT 0,
    event_floor bigint NOT NULL DEFAULT 0,
    attempt_no int NOT NULL DEFAULT 0,
    trace_id varchar(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    error_code varchar(64) CHARACTER SET ascii COLLATE ascii_bin DEFAULT NULL,
    usage_json json DEFAULT NULL,
    created_at timestamp(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    started_at timestamp(3) DEFAULT NULL,
    finished_at timestamp(3) DEFAULT NULL,
    deadline_at timestamp(3) NOT NULL,
    retain_until timestamp(3) NOT NULL,
    PRIMARY KEY(tenant_id,environment,run_id),
    UNIQUE KEY uq_oa_run_id(run_id),
    UNIQUE KEY uq_oa_run_idempotency(tenant_id,environment,deployment_id,idempotency_key),
    KEY ix_oa_run_queue(tenant_id,environment,state,created_at,run_id),
    CONSTRAINT fk_oa_run_deployment FOREIGN KEY(tenant_id,environment,deployment_id)
        REFERENCES oa_agent_deployment(tenant_id,environment,deployment_id),
    CONSTRAINT ck_oa_run_state CHECK(state IN ('queued','running','cancel_requested','succeeded','failed','timed_out','cancelled')),
    CONSTRAINT ck_oa_run_json CHECK(JSON_VALID(input_json) AND JSON_VALID(snapshot_json)),
    CONSTRAINT ck_oa_run_counters CHECK(revision>=0 AND attempt_no BETWEEN 0 AND 100
        AND last_event_seq>=0 AND event_bytes>=0 AND event_floor BETWEEN 0 AND last_event_seq),
    CONSTRAINT ck_oa_run_terminal CHECK((state IN ('succeeded','failed','timed_out','cancelled') AND finished_at IS NOT NULL)
        OR (state IN ('queued','running','cancel_requested') AND finished_at IS NULL))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

-- A slot is held until cleanup is independently confirmed. An expired heartbeat does NOT free it.
CREATE TABLE oa_run_attempt (
    tenant_id varchar(80) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    environment varchar(8) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    run_id varchar(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    attempt_no int NOT NULL,
    lease_id varchar(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    worker_id varchar(128) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    fencing_token bigint NOT NULL,
    lease_state varchar(24) NOT NULL DEFAULT 'held',
    heartbeat_until timestamp(3) NOT NULL,
    created_at timestamp(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    released_at timestamp(3) DEFAULT NULL,
    PRIMARY KEY(tenant_id,environment,run_id,attempt_no),
    UNIQUE KEY uq_oa_attempt_lease(lease_id),
    KEY ix_oa_attempt_slots(tenant_id,environment,lease_state),
    CONSTRAINT fk_oa_attempt_run FOREIGN KEY(tenant_id,environment,run_id) REFERENCES oa_agent_run(tenant_id,environment,run_id),
    CONSTRAINT ck_oa_attempt_number CHECK(attempt_no BETWEEN 1 AND 100 AND fencing_token>0),
    CONSTRAINT ck_oa_attempt_state CHECK(lease_state IN ('held','cleanup_pending','released')),
    CONSTRAINT ck_oa_attempt_release CHECK((lease_state='released' AND released_at IS NOT NULL)
        OR (lease_state<>'released' AND released_at IS NULL))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE oa_run_event (
    tenant_id varchar(80) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    environment varchar(8) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    run_id varchar(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    sequence_no bigint NOT NULL,
    event_key varchar(128) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    event_type varchar(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    payload_json longtext NOT NULL,
    payload_sha256 char(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    created_at timestamp(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    retain_until timestamp(3) NOT NULL,
    PRIMARY KEY(tenant_id,environment,run_id,sequence_no),
    UNIQUE KEY uq_oa_event_dedup(tenant_id,environment,run_id,event_key),
    KEY ix_oa_event_retention(retain_until),
    CONSTRAINT fk_oa_event_run FOREIGN KEY(tenant_id,environment,run_id) REFERENCES oa_agent_run(tenant_id,environment,run_id),
    CONSTRAINT ck_oa_event_payload CHECK(JSON_VALID(payload_json) AND sequence_no>0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

-- Contains identifiers only. Prompts and model credentials never enter the dispatch payload.
CREATE TABLE oa_run_outbox (
    id bigint NOT NULL AUTO_INCREMENT PRIMARY KEY,
    tenant_id varchar(80) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    environment varchar(8) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    run_id varchar(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    operation varchar(16) NOT NULL,
    state varchar(16) NOT NULL DEFAULT 'pending',
    available_at timestamp(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    claim_id varchar(36) CHARACTER SET ascii COLLATE ascii_bin DEFAULT NULL,
    claim_until timestamp(3) DEFAULT NULL,
    delivery_attempts int NOT NULL DEFAULT 0,
    delivered_at timestamp(3) DEFAULT NULL,
    UNIQUE KEY uq_oa_outbox_operation(tenant_id,environment,run_id,operation),
    KEY ix_oa_outbox_pending(state,available_at,id),
    CONSTRAINT fk_oa_outbox_run FOREIGN KEY(tenant_id,environment,run_id) REFERENCES oa_agent_run(tenant_id,environment,run_id),
    CONSTRAINT ck_oa_outbox_operation CHECK(operation IN ('start','cancel')),
    CONSTRAINT ck_oa_outbox_state CHECK(state IN ('pending','claimed','delivered')),
    CONSTRAINT ck_oa_outbox_attempts CHECK(delivery_attempts>=0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
