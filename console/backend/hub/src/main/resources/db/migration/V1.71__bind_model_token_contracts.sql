-- Additive, no backfill: legacy versions remain unbounded and cannot enter strict mode.
CREATE TABLE oa_model_token_bound (
    tenant_id varchar(80) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    environment varchar(8) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    connection_id varchar(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    version bigint NOT NULL,
    max_total_tokens bigint NOT NULL,
    source_ref varchar(128) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    evidence_sha256 char(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    contract_sha256 char(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    verified_at_ms bigint NOT NULL,
    valid_until_ms bigint NOT NULL,
    PRIMARY KEY (tenant_id,environment,connection_id,version),
    CONSTRAINT fk_oa_model_token_bound_version FOREIGN KEY (tenant_id,environment,connection_id,version)
        REFERENCES oa_model_connection_version (tenant_id,environment,connection_id,version),
    CONSTRAINT ck_oa_model_total_token_bound CHECK (max_total_tokens BETWEEN 1 AND 1000000000),
    CONSTRAINT ck_oa_model_token_bound_time CHECK (verified_at_ms>=946684800000 AND verified_at_ms<valid_until_ms AND valid_until_ms<4102444800000)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
