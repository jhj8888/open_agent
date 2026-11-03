-- Drafts in skill_file remain unchanged. Releases are explicitly imported or uploaded.
CREATE TABLE oa_skill_upload (
    tenant_id varchar(80) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    environment varchar(8) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    upload_id varchar(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    object_prefix varchar(512) NOT NULL,
    state varchar(16) NOT NULL DEFAULT 'uploading',
    created_at timestamp(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    expires_at timestamp(3) NOT NULL,
    PRIMARY KEY(tenant_id,upload_id),
    KEY ix_oa_skill_upload_cleanup(state,expires_at),
    CONSTRAINT fk_oa_skill_upload_env FOREIGN KEY(tenant_id,environment) REFERENCES oa_tenant_environment(tenant_id,environment),
    CONSTRAINT ck_oa_skill_upload_state CHECK(state IN ('uploading','published','failed','cleaned'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE oa_skill_release (
    tenant_id varchar(80) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    release_id varchar(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    skill_name varchar(64) NOT NULL,
    description varchar(1024) NOT NULL,
    version_label varchar(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    source_kind varchar(16) NOT NULL,
    legacy_skill_id bigint DEFAULT NULL,
    manifest_sha256 char(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    manifest_json longtext NOT NULL,
    runtime_image varchar(512) NOT NULL,
    object_prefix varchar(512) NOT NULL,
    total_bytes bigint NOT NULL,
    file_count int NOT NULL,
    status varchar(16) NOT NULL DEFAULT 'ready',
    created_by varchar(128) COLLATE utf8mb4_bin NOT NULL,
    created_at timestamp(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    retain_until timestamp(3) NOT NULL,
    PRIMARY KEY(tenant_id,release_id),
    UNIQUE KEY uq_oa_skill_version(tenant_id,skill_name,version_label),
    KEY ix_oa_skill_legacy(tenant_id,legacy_skill_id),
    CONSTRAINT fk_oa_skill_release_tenant FOREIGN KEY(tenant_id) REFERENCES oa_tenant(tenant_id),
    CONSTRAINT ck_oa_skill_release_state CHECK(status IN ('ready','revoked','deleted')),
    CONSTRAINT ck_oa_skill_release_source CHECK(source_kind IN ('upload','legacy-import')),
    CONSTRAINT ck_oa_skill_release_size CHECK(total_bytes>=0 AND total_bytes<=20971520 AND file_count BETWEEN 1 AND 100),
    CONSTRAINT ck_oa_skill_manifest_json CHECK(JSON_VALID(manifest_json))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE oa_skill_release_file (
    tenant_id varchar(80) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    release_id varchar(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    file_index int NOT NULL,
    file_path varchar(1024) NOT NULL,
    path_sha256 char(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    content_sha256 char(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    size_bytes bigint NOT NULL,
    object_key varchar(512) NOT NULL,
    PRIMARY KEY(tenant_id,release_id,file_index),
    UNIQUE KEY uq_oa_skill_file_path(tenant_id,release_id,path_sha256),
    CONSTRAINT fk_oa_skill_file_release FOREIGN KEY(tenant_id,release_id) REFERENCES oa_skill_release(tenant_id,release_id),
    CONSTRAINT ck_oa_skill_file_size CHECK(size_bytes BETWEEN 0 AND 5242880)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE oa_skill_release_environment (
    tenant_id varchar(80) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    environment varchar(8) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    release_id varchar(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    granted_by varchar(128) COLLATE utf8mb4_bin NOT NULL,
    granted_at timestamp(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    PRIMARY KEY(tenant_id,environment,release_id),
    CONSTRAINT fk_oa_skill_grant_env FOREIGN KEY(tenant_id,environment) REFERENCES oa_tenant_environment(tenant_id,environment),
    CONSTRAINT fk_oa_skill_grant_release FOREIGN KEY(tenant_id,release_id) REFERENCES oa_skill_release(tenant_id,release_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

-- Only the trusted run completion transaction may write successful test evidence.
CREATE TABLE oa_skill_verification (
    tenant_id varchar(80) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    release_id varchar(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    run_id varchar(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    manifest_sha256 char(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    passed boolean NOT NULL,
    created_at timestamp(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    PRIMARY KEY(tenant_id,release_id,run_id),
    CONSTRAINT fk_oa_skill_test_release FOREIGN KEY(tenant_id,release_id) REFERENCES oa_skill_release(tenant_id,release_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE oa_skill_release_reference (
    tenant_id varchar(80) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    release_id varchar(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    reference_type varchar(16) NOT NULL,
    reference_id varchar(80) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    retain_until timestamp(3) DEFAULT NULL,
    PRIMARY KEY(tenant_id,release_id,reference_type,reference_id),
    CONSTRAINT fk_oa_skill_reference FOREIGN KEY(tenant_id,release_id) REFERENCES oa_skill_release(tenant_id,release_id),
    CONSTRAINT ck_oa_skill_reference_type CHECK(reference_type IN ('run','deployment'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
