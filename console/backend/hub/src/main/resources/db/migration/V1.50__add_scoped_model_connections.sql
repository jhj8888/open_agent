-- Additive: legacy model IDs and rows remain intact. Connections are explicitly
-- registered in an authorized environment before use by platform runs.
CREATE TABLE oa_model_connection (
    tenant_id varchar(80) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    environment varchar(8) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    connection_id varchar(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    display_name varchar(120) NOT NULL,
    current_version bigint NOT NULL,
    enabled boolean NOT NULL DEFAULT true,
    created_by varchar(128) COLLATE utf8mb4_bin NOT NULL,
    created_at timestamp(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    updated_at timestamp(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    PRIMARY KEY (tenant_id, environment, connection_id),
    CONSTRAINT fk_oa_model_env FOREIGN KEY (tenant_id, environment)
        REFERENCES oa_tenant_environment (tenant_id, environment)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE oa_model_connection_version (
    tenant_id varchar(80) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    environment varchar(8) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    connection_id varchar(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    version bigint NOT NULL,
    endpoint varchar(2048) NOT NULL,
    model_name varchar(255) NOT NULL,
    protocol varchar(32) NOT NULL DEFAULT 'openai',
    supports_stream boolean NOT NULL,
    supports_tools boolean NOT NULL,
    supports_usage boolean NOT NULL,
    connect_timeout_seconds int NOT NULL,
    read_timeout_seconds int NOT NULL,
    total_timeout_seconds int NOT NULL,
    ca_ref varchar(128) DEFAULT NULL,
    credential_ref varchar(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    credential_key_id varchar(80) NOT NULL,
    credential_nonce binary(12) NOT NULL,
    credential_cipher varbinary(8192) NOT NULL,
    revoked boolean NOT NULL DEFAULT false,
    created_at timestamp(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    PRIMARY KEY (tenant_id, environment, connection_id, version),
    UNIQUE KEY uq_oa_model_credential_ref (credential_ref),
    CONSTRAINT fk_oa_model_version FOREIGN KEY (tenant_id, environment, connection_id)
        REFERENCES oa_model_connection (tenant_id, environment, connection_id),
    CONSTRAINT ck_oa_model_protocol CHECK (protocol='openai'),
    CONSTRAINT ck_oa_model_timeout CHECK (connect_timeout_seconds BETWEEN 1 AND 60
        AND read_timeout_seconds BETWEEN 1 AND 1800 AND total_timeout_seconds BETWEEN 1 AND 3600
        AND total_timeout_seconds >= read_timeout_seconds)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
