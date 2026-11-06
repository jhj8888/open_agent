-- Internal accounting only. No caller is switched to enforcement by this migration.
CREATE TABLE oa_model_token_day (
    tenant_id varchar(80) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    environment varchar(8) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    budget_day date NOT NULL,
    held_tokens decimal(30,0) NOT NULL DEFAULT 0,
    spent_tokens decimal(30,0) NOT NULL DEFAULT 0,
    PRIMARY KEY (tenant_id,environment,budget_day),
    CONSTRAINT fk_oa_token_day_environment FOREIGN KEY (tenant_id,environment)
        REFERENCES oa_tenant_environment (tenant_id,environment),
    CONSTRAINT ck_oa_token_day_amounts CHECK (held_tokens>=0 AND spent_tokens>=0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE oa_model_token_reservation (
    tenant_id varchar(80) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    environment varchar(8) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    call_id varchar(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    request_sha256 char(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    budget_day date NOT NULL,
    bound_tokens bigint NOT NULL,
    phase varchar(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    input_tokens bigint DEFAULT NULL,
    output_tokens bigint DEFAULT NULL,
    prepared_at timestamp(3) NOT NULL,
    dispatched_at timestamp(3) DEFAULT NULL,
    settled_at timestamp(3) DEFAULT NULL,
    PRIMARY KEY (tenant_id,environment,call_id),
    KEY ix_oa_token_reservation_day (tenant_id,environment,budget_day),
    KEY ix_oa_token_reservation_phase (tenant_id,environment,phase),
    CONSTRAINT fk_oa_token_reservation_day FOREIGN KEY (tenant_id,environment,budget_day)
        REFERENCES oa_model_token_day (tenant_id,environment,budget_day),
    CONSTRAINT ck_oa_token_bound CHECK (bound_tokens BETWEEN 1 AND 1000000000),
    CONSTRAINT ck_oa_token_phase CHECK (phase IN ('prepared','dispatched','unknown','settled','cancelled','breached')),
    CONSTRAINT ck_oa_token_dispatch CHECK (
        (phase IN ('prepared','cancelled') AND dispatched_at IS NULL)
        OR (phase IN ('dispatched','unknown','settled','breached') AND dispatched_at IS NOT NULL)),
    CONSTRAINT ck_oa_token_settlement CHECK (
        (phase IN ('prepared','dispatched','unknown','cancelled') AND input_tokens IS NULL AND output_tokens IS NULL AND settled_at IS NULL)
        OR (phase IN ('settled','breached') AND input_tokens IS NOT NULL AND output_tokens IS NOT NULL AND settled_at IS NOT NULL
            AND input_tokens BETWEEN 0 AND 1000000000 AND output_tokens BETWEEN 0 AND 1000000000)),
    CONSTRAINT ck_oa_token_breach CHECK (
        phase NOT IN ('settled','breached')
        OR (phase='settled' AND input_tokens+output_tokens<=bound_tokens)
        OR (phase='breached' AND input_tokens+output_tokens>bound_tokens))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
