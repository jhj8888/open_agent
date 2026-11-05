-- Cluster execution uses its own resource identity, not systemd boot/unit fields.
CREATE TABLE oa_run_cluster_execution (
    lease_id varchar(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL PRIMARY KEY,
    supervisor_id varchar(128) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    cluster_id varchar(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    namespace_name varchar(63) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    namespace_uid varchar(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    executor_image varchar(512) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    pod_name varchar(63) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    pod_uid varchar(36) CHARACTER SET ascii COLLATE ascii_bin DEFAULT NULL,
    observed_resource_version varchar(128) CHARACTER SET ascii COLLATE ascii_bin DEFAULT NULL,
    phase varchar(16) NOT NULL DEFAULT 'prepared',
    created_at timestamp(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    launch_started_at timestamp(3) DEFAULT NULL,
    observed_at timestamp(3) DEFAULT NULL,
    terminated_at timestamp(3) DEFAULT NULL,
    termination_id varchar(36) CHARACTER SET ascii COLLATE ascii_bin DEFAULT NULL,
    termination_json json DEFAULT NULL,
    released_at timestamp(3) DEFAULT NULL,
    proof_id varchar(36) CHARACTER SET ascii COLLATE ascii_bin DEFAULT NULL,
    proof_json json DEFAULT NULL,
    UNIQUE KEY uq_oa_cluster_name (cluster_id,namespace_uid,pod_name),
    UNIQUE KEY uq_oa_cluster_uid (cluster_id,namespace_uid,pod_uid),
    UNIQUE KEY uq_oa_cluster_termination (termination_id),
    UNIQUE KEY uq_oa_cluster_proof (proof_id),
    KEY ix_oa_cluster_pending (supervisor_id,phase,lease_id),
    CONSTRAINT fk_oa_cluster_attempt FOREIGN KEY (lease_id) REFERENCES oa_run_attempt(lease_id),
    CONSTRAINT ck_oa_cluster_phase CHECK (phase IN ('prepared','launching','observed','terminated','released')),
    CONSTRAINT ck_oa_cluster_launch CHECK (
        (phase='prepared' AND launch_started_at IS NULL AND pod_uid IS NULL AND observed_at IS NULL)
        OR (phase='launching' AND launch_started_at IS NOT NULL AND pod_uid IS NULL AND observed_at IS NULL)
        OR (phase IN ('observed','terminated') AND launch_started_at IS NOT NULL AND pod_uid IS NOT NULL
            AND observed_resource_version IS NOT NULL AND observed_at IS NOT NULL)
        OR phase='released'),
    CONSTRAINT ck_oa_cluster_termination CHECK (
        (phase IN ('prepared','launching','observed') AND terminated_at IS NULL AND termination_id IS NULL AND termination_json IS NULL)
        OR (phase='terminated' AND terminated_at IS NOT NULL AND termination_id IS NOT NULL AND termination_json IS NOT NULL)
        OR phase='released'),
    CONSTRAINT ck_oa_cluster_release CHECK (
        (phase='released' AND released_at IS NOT NULL AND proof_id IS NOT NULL AND proof_json IS NOT NULL)
        OR (phase<>'released' AND released_at IS NULL AND proof_id IS NULL AND proof_json IS NULL))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
