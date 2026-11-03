-- Additive migration: never rewrite upstream spaces, users, or resource rows.
-- Production grants must be assigned explicitly after reviewing the migration report.
CREATE TABLE oa_tenant (
    tenant_id varchar(80) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    display_name varchar(255) NOT NULL,
    scope_kind varchar(16) NOT NULL,
    space_id bigint DEFAULT NULL,
    personal_uid varchar(128) COLLATE utf8mb4_bin DEFAULT NULL,
    status varchar(16) NOT NULL DEFAULT 'active',
    revision bigint NOT NULL DEFAULT 0,
    created_at timestamp(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    updated_at timestamp(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    PRIMARY KEY (tenant_id),
    UNIQUE KEY uq_oa_tenant_space (space_id),
    UNIQUE KEY uq_oa_tenant_personal (personal_uid),
    CONSTRAINT ck_oa_tenant_scope CHECK (
        (scope_kind = 'space' AND space_id IS NOT NULL AND personal_uid IS NULL)
        OR (scope_kind = 'personal' AND space_id IS NULL AND personal_uid IS NOT NULL)),
    CONSTRAINT ck_oa_tenant_status CHECK (status IN ('active', 'suspended', 'archived'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE oa_tenant_environment (
    tenant_id varchar(80) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    environment varchar(8) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    max_concurrent_runs int NOT NULL DEFAULT 4,
    max_queued_runs int NOT NULL DEFAULT 32,
    max_sandboxes int NOT NULL DEFAULT 4,
    daily_token_budget bigint NOT NULL DEFAULT 1000000,
    audit_retention_days int NOT NULL DEFAULT 180,
    revision bigint NOT NULL DEFAULT 0,
    PRIMARY KEY (tenant_id, environment),
    CONSTRAINT fk_oa_environment_tenant FOREIGN KEY (tenant_id) REFERENCES oa_tenant (tenant_id),
    CONSTRAINT ck_oa_environment CHECK (environment IN ('test', 'prod')),
    CONSTRAINT ck_oa_quota CHECK (max_concurrent_runs >= 0 AND max_queued_runs >= 0
        AND max_sandboxes >= 0 AND daily_token_budget >= 0 AND audit_retention_days >= 1)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE oa_tenant_member (
    tenant_id varchar(80) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    environment varchar(8) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    uid varchar(128) COLLATE utf8mb4_bin NOT NULL,
    role varchar(24) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    source varchar(16) NOT NULL DEFAULT 'explicit',
    revision bigint NOT NULL DEFAULT 0,
    created_at timestamp(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    updated_at timestamp(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    PRIMARY KEY (tenant_id, environment, uid),
    KEY ix_oa_member_uid (uid, environment),
    CONSTRAINT fk_oa_member_environment FOREIGN KEY (tenant_id, environment)
        REFERENCES oa_tenant_environment (tenant_id, environment),
    CONSTRAINT ck_oa_member_role CHECK (role IN ('tenant_admin', 'developer', 'operator', 'viewer')),
    CONSTRAINT ck_oa_member_source CHECK (source IN ('migration', 'explicit'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

-- No default admin account or special name: deployment bootstrap explicitly enrolls a verified UID.
CREATE TABLE oa_platform_admin (
    uid varchar(128) COLLATE utf8mb4_bin NOT NULL PRIMARY KEY,
    created_at timestamp(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE oa_tenant_audit (
    id bigint NOT NULL AUTO_INCREMENT PRIMARY KEY,
    tenant_id varchar(80) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    environment varchar(8) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    actor_uid varchar(128) COLLATE utf8mb4_bin NOT NULL,
    action varchar(64) NOT NULL,
    subject_id varchar(255) NOT NULL,
    before_value json DEFAULT NULL,
    after_value json DEFAULT NULL,
    created_at timestamp(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    retain_until timestamp(3) NOT NULL,
    KEY ix_oa_audit_scope (tenant_id, environment, id),
    KEY ix_oa_audit_retention (retain_until),
    CONSTRAINT fk_oa_audit_environment FOREIGN KEY (tenant_id, environment)
        REFERENCES oa_tenant_environment (tenant_id, environment)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

INSERT INTO oa_tenant (tenant_id, display_name, scope_kind, space_id)
SELECT CONCAT('space-', id), name, 'space', id FROM agent_space WHERE deleted = 0;

INSERT INTO oa_tenant (tenant_id, display_name, scope_kind, personal_uid)
SELECT CONCAT('user-', SHA2(uid, 256)), COALESCE(NULLIF(username, ''), uid), 'personal', uid
FROM user_info WHERE deleted = 0 AND uid IS NOT NULL AND uid <> '';

INSERT INTO oa_tenant_environment (tenant_id, environment)
SELECT tenant_id, 'test' FROM oa_tenant UNION ALL SELECT tenant_id, 'prod' FROM oa_tenant;

INSERT INTO oa_tenant_member (tenant_id, environment, uid, role, source)
SELECT t.tenant_id, 'test', su.uid,
       CASE WHEN su.role IN (1, 2) THEN 'tenant_admin' ELSE 'developer' END, 'migration'
FROM oa_tenant t JOIN agent_space_user su ON su.space_id = t.space_id
JOIN user_info u ON u.uid COLLATE utf8mb4_bin = su.uid COLLATE utf8mb4_bin
WHERE u.deleted = 0 AND su.role IN (1, 2, 3);

INSERT INTO oa_tenant_member (tenant_id, environment, uid, role, source)
SELECT tenant_id, 'test', personal_uid, 'tenant_admin', 'migration'
FROM oa_tenant WHERE scope_kind = 'personal';

INSERT INTO oa_tenant_audit
    (tenant_id, environment, actor_uid, action, subject_id, after_value, retain_until)
SELECT tenant_id, 'test', 'system:migration:1.49', 'tenant.migrated', tenant_id,
       JSON_OBJECT('scope_kind', scope_kind, 'production_grants', 0),
       TIMESTAMPADD(DAY, 180, CURRENT_TIMESTAMP(3)) FROM oa_tenant;
