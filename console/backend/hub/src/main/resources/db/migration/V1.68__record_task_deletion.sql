-- Keep task identity, accounting and cleanup evidence after explicit administrator deletion.
ALTER TABLE oa_run_output ADD KEY ix_oa_output_run(run_id);
CREATE TABLE oa_run_deletion (
    run_id varchar(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL PRIMARY KEY,
    tenant_id varchar(80) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    environment varchar(8) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    actor_id varchar(128) NOT NULL,
    deleted_at timestamp(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    purged_at timestamp(3) DEFAULT NULL,
    KEY ix_oa_run_deletion_cleanup(purged_at,deleted_at),
    CONSTRAINT fk_oa_run_deletion_run FOREIGN KEY(tenant_id,environment,run_id)
        REFERENCES oa_agent_run(tenant_id,environment,run_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
