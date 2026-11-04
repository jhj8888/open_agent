-- Explicit production invocation grants and individually revocable deployment credentials.
CREATE TABLE oa_deployment_grant (
    tenant_id varchar(80) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    environment varchar(8) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    deployment_id varchar(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    uid varchar(128) COLLATE utf8mb4_bin NOT NULL,
    granted_by varchar(128) COLLATE utf8mb4_bin NOT NULL,
    created_at timestamp(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    PRIMARY KEY(tenant_id,environment,deployment_id,uid),
    CONSTRAINT fk_oa_deployment_grant FOREIGN KEY(tenant_id,environment,deployment_id)
        REFERENCES oa_agent_deployment(tenant_id,environment,deployment_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE oa_deployment_credential (
    key_id varchar(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL PRIMARY KEY,
    subject_id varchar(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    tenant_id varchar(80) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    environment varchar(8) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    deployment_id varchar(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    display_name varchar(128) NOT NULL,
    secret_sha256 char(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    can_create boolean NOT NULL,
    can_read boolean NOT NULL,
    can_cancel boolean NOT NULL,
    revoked boolean NOT NULL DEFAULT false,
    created_by varchar(128) COLLATE utf8mb4_bin NOT NULL,
    created_at timestamp(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    expires_at timestamp(3) NOT NULL,
    revoked_at timestamp(3) DEFAULT NULL,
    KEY ix_oa_deployment_credentials(tenant_id,environment,deployment_id,created_at),
    CONSTRAINT fk_oa_deployment_credential FOREIGN KEY(tenant_id,environment,deployment_id)
        REFERENCES oa_agent_deployment(tenant_id,environment,deployment_id),
    CONSTRAINT ck_oa_deployment_credential_actions CHECK(can_create OR can_read OR can_cancel),
    CONSTRAINT ck_oa_deployment_credential_revocation CHECK((revoked=true AND revoked_at IS NOT NULL)
        OR (revoked=false AND revoked_at IS NULL))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE oa_deployment_promotion (
    tenant_id varchar(80) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    source_environment varchar(8) CHARACTER SET ascii COLLATE ascii_bin NOT NULL DEFAULT 'test',
    source_deployment_id varchar(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    target_environment varchar(8) CHARACTER SET ascii COLLATE ascii_bin NOT NULL DEFAULT 'prod',
    target_deployment_id varchar(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    verification_run_id varchar(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    approved_by varchar(128) COLLATE utf8mb4_bin NOT NULL,
    approved_at timestamp(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    PRIMARY KEY(target_deployment_id),
    CONSTRAINT fk_oa_promotion_source FOREIGN KEY(tenant_id,source_environment,source_deployment_id)
        REFERENCES oa_agent_deployment(tenant_id,environment,deployment_id),
    CONSTRAINT fk_oa_promotion_target FOREIGN KEY(tenant_id,target_environment,target_deployment_id)
        REFERENCES oa_agent_deployment(tenant_id,environment,deployment_id),
    CONSTRAINT fk_oa_promotion_run FOREIGN KEY(tenant_id,source_environment,verification_run_id)
        REFERENCES oa_agent_run(tenant_id,environment,run_id),
    CONSTRAINT ck_oa_promotion_environments CHECK(source_environment='test' AND target_environment='prod')
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
