-- Content quota is distinct from step count; reaching it must not stop execution.
ALTER TABLE oa_run_attempt
    ADD COLUMN observation_content_bytes BIGINT NOT NULL DEFAULT 0,
    ADD CONSTRAINT ck_observation_content_bytes CHECK (observation_content_bytes BETWEEN 0 AND 4194304);
