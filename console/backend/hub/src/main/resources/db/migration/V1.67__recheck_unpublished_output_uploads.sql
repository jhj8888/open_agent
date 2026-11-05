-- A successful delete cannot prove a still-running upload will never commit later.
-- Retain exact-key tombstones and periodically recheck them; published keys are excluded.
ALTER TABLE oa_run_output_upload
    ADD COLUMN reconcile_after timestamp(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    ADD KEY ix_oa_output_upload_due(state,reconcile_after,created_at);
