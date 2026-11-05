ALTER TABLE oa_run_outbox
    ADD KEY ix_oa_outbox_env_pending (environment,state,available_at,id),
    ADD KEY ix_oa_outbox_env_expired (environment,state,claim_until,id);
