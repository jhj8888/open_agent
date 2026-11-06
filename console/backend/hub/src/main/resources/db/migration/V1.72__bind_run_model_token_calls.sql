-- Owned model-call identity; never a caller-supplied token bound or an enable switch.
CREATE TABLE oa_run_model_token_call (
    tenant_id varchar(80) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    environment varchar(8) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    call_id varchar(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    run_id varchar(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    attempt_no int NOT NULL,
    lease_id varchar(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    request_sha256 char(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    model_connection_id varchar(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    model_version bigint NOT NULL,
    contract_sha256 char(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    ledger_fingerprint char(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    PRIMARY KEY (tenant_id,environment,call_id),
    KEY ix_oa_run_model_calls (tenant_id,environment,run_id,attempt_no),
    CONSTRAINT fk_oa_run_model_call_reservation FOREIGN KEY (tenant_id,environment,call_id)
        REFERENCES oa_model_token_reservation (tenant_id,environment,call_id),
    CONSTRAINT fk_oa_run_model_call_attempt FOREIGN KEY (tenant_id,environment,run_id,attempt_no)
        REFERENCES oa_run_attempt (tenant_id,environment,run_id,attempt_no),
    CONSTRAINT fk_oa_run_model_call_lease FOREIGN KEY (lease_id) REFERENCES oa_run_attempt (lease_id),
    CONSTRAINT fk_oa_run_model_call_contract FOREIGN KEY (tenant_id,environment,model_connection_id,model_version)
        REFERENCES oa_model_token_bound (tenant_id,environment,connection_id,version)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
