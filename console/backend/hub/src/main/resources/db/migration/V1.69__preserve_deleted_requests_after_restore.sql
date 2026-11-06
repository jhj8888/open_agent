-- Recovery can retain deletion evidence for tasks created after the restored snapshot.
-- Deliberately no FK to the older task/deployment/tenant snapshot. This table grants
-- no access; it only prevents a previously deleted request from executing again.
CREATE TABLE oa_run_deletion_fence (
    run_id varchar(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL PRIMARY KEY,
    tenant_id varchar(80) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    environment varchar(8) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    deployment_id varchar(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    idempotency_key varchar(128) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    request_sha256 char(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    snapshot_sha256 char(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    run_actor_id varchar(128) COLLATE utf8mb4_bin NOT NULL,
    deleted_by varchar(128) COLLATE utf8mb4_bin NOT NULL,
    deleted_at timestamp(3) NOT NULL,
    restored_at timestamp(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    UNIQUE KEY uq_oa_deleted_request(tenant_id,environment,deployment_id,idempotency_key),
    CONSTRAINT ck_oa_deleted_request_environment CHECK(environment IN ('test','prod'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
