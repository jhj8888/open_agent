-- Positive pre-storage API rejection receipts; unknown creates still retain quota.
ALTER TABLE oa_run_cluster_execution
    ADD COLUMN creation_rejection_id varchar(36) CHARACTER SET ascii COLLATE ascii_bin DEFAULT NULL,
    ADD COLUMN creation_rejection_json json DEFAULT NULL,
    ADD COLUMN creation_rejected_at timestamp(3) DEFAULT NULL,
    ADD UNIQUE KEY uq_oa_cluster_creation_rejection (creation_rejection_id),
    DROP CHECK ck_oa_cluster_phase,
    DROP CHECK ck_oa_cluster_launch,
    DROP CHECK ck_oa_cluster_termination,
    DROP CHECK ck_oa_cluster_not_started,
    ADD CONSTRAINT ck_oa_cluster_phase CHECK (phase IN ('prepared','launching','observed','terminated','not_started','rejected','released')),
    ADD CONSTRAINT ck_oa_cluster_launch CHECK (
        (phase='prepared' AND launch_started_at IS NULL AND pod_uid IS NULL AND observed_at IS NULL)
        OR (phase IN ('launching','rejected') AND launch_started_at IS NOT NULL AND pod_uid IS NULL AND observed_at IS NULL)
        OR (phase IN ('observed','terminated','not_started') AND launch_started_at IS NOT NULL AND pod_uid IS NOT NULL
            AND observed_resource_version IS NOT NULL AND observed_at IS NOT NULL)
        OR phase='released'),
    ADD CONSTRAINT ck_oa_cluster_termination CHECK (
        (phase IN ('prepared','launching','observed','not_started','rejected') AND terminated_at IS NULL AND termination_id IS NULL AND termination_json IS NULL)
        OR (phase='terminated' AND terminated_at IS NOT NULL AND termination_id IS NOT NULL AND termination_json IS NOT NULL)
        OR phase='released'),
    ADD CONSTRAINT ck_oa_cluster_not_started CHECK (
        (phase IN ('prepared','launching','observed','terminated','rejected') AND not_started_at IS NULL AND not_started_id IS NULL AND not_started_json IS NULL)
        OR (phase='not_started' AND not_started_at IS NOT NULL AND not_started_id IS NOT NULL AND not_started_json IS NOT NULL)
        OR phase='released'),
    ADD CONSTRAINT ck_oa_cluster_creation_rejection CHECK (
        (phase='rejected' AND creation_rejection_id IS NOT NULL AND creation_rejection_json IS NOT NULL AND creation_rejected_at IS NOT NULL)
        OR (phase NOT IN ('rejected','released') AND creation_rejection_id IS NULL AND creation_rejection_json IS NULL AND creation_rejected_at IS NULL)
        OR phase='released'),
    ADD CONSTRAINT ck_oa_cluster_rejection_exclusive CHECK (
        creation_rejection_id IS NULL OR (pod_uid IS NULL AND observed_resource_version IS NULL AND observed_at IS NULL
            AND termination_id IS NULL AND not_started_id IS NULL));
