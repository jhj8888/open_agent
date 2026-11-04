-- Windowed tenant aggregates and fixed-cardinality operational metrics.
CREATE INDEX ix_oa_run_monitor_window ON oa_agent_run(tenant_id,environment,created_at);
CREATE INDEX ix_oa_run_monitor_active ON oa_agent_run(environment,state,created_at);
CREATE INDEX ix_oa_run_monitor_finished ON oa_agent_run(environment,finished_at);
CREATE INDEX ix_oa_attempt_monitor ON oa_run_attempt(environment,lease_state,heartbeat_until);
CREATE INDEX ix_oa_sandbox_monitor ON oa_run_sandbox(environment,phase,reserved_at);
