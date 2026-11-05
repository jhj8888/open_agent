-- Output retention follows the task, never event/trace TTL or sandbox deletion.
CREATE TABLE oa_run_output (
    output_id char(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL PRIMARY KEY,
    tenant_id varchar(80) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    environment varchar(8) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    run_id varchar(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    attempt_no int NOT NULL,
    step_id varchar(128) NOT NULL,
    source_path varchar(512) NOT NULL,
    file_name varchar(255) NOT NULL,
    size_bytes bigint NOT NULL,
    sha256 char(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    fingerprint char(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    state varchar(16) NOT NULL DEFAULT 'pending',
    object_key varchar(255) CHARACTER SET ascii COLLATE ascii_bin DEFAULT NULL,
    created_at timestamp(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    published_at timestamp(3) DEFAULT NULL,
    UNIQUE KEY uq_oa_output_content(tenant_id,environment,run_id,fingerprint),
    KEY ix_oa_output_task(tenant_id,environment,run_id,created_at),
    CONSTRAINT fk_oa_output_run FOREIGN KEY(tenant_id,environment,run_id) REFERENCES oa_agent_run(tenant_id,environment,run_id),
    CONSTRAINT ck_oa_output_state CHECK(state IN ('pending','available','failed')),
    CONSTRAINT ck_oa_output_size CHECK(size_bytes BETWEEN 0 AND 33554432),
    CONSTRAINT ck_oa_output_published CHECK((state='available' AND object_key IS NOT NULL AND published_at IS NOT NULL) OR (state<>'available' AND object_key IS NULL))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

-- Each physical upload has an immutable private object key. Retries never overwrite it.
CREATE TABLE oa_run_output_upload (
    upload_id char(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL PRIMARY KEY,
    output_id char(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    object_key varchar(255) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    state varchar(16) NOT NULL DEFAULT 'writing',
    created_at timestamp(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    KEY ix_oa_output_upload_reconcile(state,created_at),
    CONSTRAINT fk_oa_output_upload FOREIGN KEY(output_id) REFERENCES oa_run_output(output_id),
    CONSTRAINT ck_oa_output_upload_state CHECK(state IN ('writing','published','abandoned','reaping','cleared'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE oa_run_output_access (
    id bigint NOT NULL AUTO_INCREMENT PRIMARY KEY,
    output_id char(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    actor_id varchar(128) NOT NULL,
    action varchar(16) NOT NULL,
    created_at timestamp(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    CONSTRAINT fk_oa_output_access FOREIGN KEY(output_id) REFERENCES oa_run_output(output_id),
    CONSTRAINT ck_oa_output_action CHECK(action IN ('download','preview'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
