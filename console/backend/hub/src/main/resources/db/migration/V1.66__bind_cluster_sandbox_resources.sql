-- Independent sandbox identity; never overload trusted executor Pod/namespace fields.
ALTER TABLE oa_run_cluster_execution
    ADD COLUMN sandbox_target_json text DEFAULT NULL,
    ADD COLUMN sandbox_resource_json text DEFAULT NULL,
    ADD COLUMN sandbox_workload_uid varchar(36) CHARACTER SET ascii COLLATE ascii_bin DEFAULT NULL,
    ADD COLUMN sandbox_pod_uid varchar(36) CHARACTER SET ascii COLLATE ascii_bin DEFAULT NULL,
    ADD COLUMN sandbox_bound_at timestamp(3) DEFAULT NULL,
    ADD UNIQUE KEY uq_oa_cluster_sandbox_workload (cluster_id,sandbox_workload_uid),
    ADD UNIQUE KEY uq_oa_cluster_sandbox_pod (cluster_id,sandbox_pod_uid),
    ADD CONSTRAINT ck_oa_cluster_sandbox_target CHECK (
        sandbox_target_json IS NULL OR (JSON_VALID(sandbox_target_json) AND JSON_TYPE(sandbox_target_json)='OBJECT')),
    ADD CONSTRAINT ck_oa_cluster_sandbox_resource CHECK (
        (sandbox_resource_json IS NULL AND sandbox_workload_uid IS NULL AND sandbox_pod_uid IS NULL AND sandbox_bound_at IS NULL)
        OR (sandbox_resource_json IS NOT NULL AND sandbox_target_json IS NOT NULL AND JSON_VALID(sandbox_resource_json)
            AND sandbox_workload_uid IS NOT NULL AND sandbox_pod_uid IS NOT NULL AND sandbox_bound_at IS NOT NULL
            AND JSON_CONTAINS_PATH(sandbox_resource_json,'all','$.workloadUid','$.podUid')=1
            AND JSON_TYPE(JSON_EXTRACT(sandbox_resource_json,'$.workloadUid'))='STRING'
            AND JSON_TYPE(JSON_EXTRACT(sandbox_resource_json,'$.podUid'))='STRING'
            AND JSON_UNQUOTE(JSON_EXTRACT(sandbox_resource_json,'$.workloadUid'))=sandbox_workload_uid
            AND JSON_UNQUOTE(JSON_EXTRACT(sandbox_resource_json,'$.podUid'))=sandbox_pod_uid));
