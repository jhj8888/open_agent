ALTER TABLE oa_run_cluster_execution
    ADD COLUMN not_started_id varchar(36) CHARACTER SET ascii COLLATE ascii_bin DEFAULT NULL,
    ADD COLUMN not_started_json json DEFAULT NULL,
    ADD COLUMN not_started_at timestamp(3) DEFAULT NULL,
    ADD UNIQUE KEY uq_oa_cluster_not_started (not_started_id),
    DROP CHECK ck_oa_cluster_phase,
    DROP CHECK ck_oa_cluster_launch,
    DROP CHECK ck_oa_cluster_termination,
    ADD CONSTRAINT ck_oa_cluster_phase CHECK (phase IN ('prepared','launching','observed','terminated','not_started','released')),
    ADD CONSTRAINT ck_oa_cluster_launch CHECK (
        (phase='prepared' AND launch_started_at IS NULL AND pod_uid IS NULL AND observed_at IS NULL)
        OR (phase='launching' AND launch_started_at IS NOT NULL AND pod_uid IS NULL AND observed_at IS NULL)
        OR (phase IN ('observed','terminated','not_started') AND launch_started_at IS NOT NULL AND pod_uid IS NOT NULL
            AND observed_resource_version IS NOT NULL AND observed_at IS NOT NULL)
        OR phase='released'),
    ADD CONSTRAINT ck_oa_cluster_termination CHECK (
        (phase IN ('prepared','launching','observed','not_started') AND terminated_at IS NULL AND termination_id IS NULL AND termination_json IS NULL)
        OR (phase='terminated' AND terminated_at IS NOT NULL AND termination_id IS NOT NULL AND termination_json IS NOT NULL)
        OR phase='released'),
    ADD CONSTRAINT ck_oa_cluster_not_started CHECK (
        (phase IN ('prepared','launching','observed','terminated') AND not_started_at IS NULL AND not_started_id IS NULL AND not_started_json IS NULL)
        OR (phase='not_started' AND not_started_at IS NOT NULL AND not_started_id IS NOT NULL AND not_started_json IS NOT NULL)
        OR phase='released'),
    ADD CONSTRAINT ck_oa_cluster_stop_exclusive CHECK (NOT (termination_id IS NOT NULL AND not_started_id IS NOT NULL));
