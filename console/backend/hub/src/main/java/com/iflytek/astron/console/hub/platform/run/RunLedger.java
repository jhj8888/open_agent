package com.iflytek.astron.console.hub.platform.run;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.nio.charset.StandardCharsets;
import java.sql.Timestamp;
import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import java.util.UUID;
import java.util.function.Supplier;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.TransactionDefinition;
import org.springframework.transaction.support.TransactionTemplate;

/**
 * Internal persistence boundary, NOT an HTTP authorization boundary. Callers must resolve
 * trusted scope and identity first. User mutations also recheck authorization under the tenant
 * lock, through the supplied callback. Worker methods require a trusted worker transport.
 * Lock order: tenant -> environment -> run -> attempt. No network I/O inside transactions.
 */
@Component
public class RunLedger {
    private static final Logger LOG = LoggerFactory.getLogger(RunLedger.class);
    private static final String RUN_COLUMNS = "run_id,deployment_id,state,revision,last_event_seq,event_floor,attempt_no,"
            + "trace_id,error_code,usage_json,created_at,started_at,finished_at,deadline_at,retain_until,snapshot_sha256";
    private static final Set<String> WORKER_ERRORS = Set.of("model_failed", "tool_failed", "worker_lost",
            "outcome_unknown", "deadline_exceeded", "execution_failed", "configuration_unavailable");
    private final JdbcTemplate jdbc;
    private final TransactionTemplate transaction;
    private final ObjectMapper json;

    public RunLedger(JdbcTemplate jdbc, PlatformTransactionManager manager, ObjectMapper json) {
        this.jdbc = jdbc;
        this.json = json;
        transaction = new TransactionTemplate(manager);
        transaction.setIsolationLevel(TransactionDefinition.ISOLATION_READ_COMMITTED);
        transaction.setTimeout(15);
    }

    public record Scope(String tenant, String environment) {
        public Scope {
            if (tenant == null || !tenant.matches("[A-Za-z0-9_-]{1,80}")
                    || !Set.of("test", "prod").contains(environment == null ? "" : environment)) throw new RunFailure("invalid_scope");
        }
    }
    public record Accepted(String runId, boolean reused) {}
    public record Lease(Scope scope, String runId, int attempt, String leaseId, long fencingToken, String workerId) {}
    public record EventPage(List<Map<String, Object>> events, long lastSequence, boolean terminal) {}

    public Accepted submit(Scope scope, String deploymentId, String actor, String key, JsonNode input,
            int timeoutSeconds, Runnable authorize) {
        uuid(deploymentId);
        if (actor == null || actor.isBlank() || actor.length() > 128 || key == null || !key.matches("[!-~]{1,128}")
                || timeoutSeconds < 1 || timeoutSeconds > 3600) throw new RunFailure("invalid_request");
        String body = RunJson.encode(json, input, 65536);
        String fingerprint = RunJson.hash(timeoutSeconds + "\n" + body);
        Accepted result = tx(() -> {
            Map<String, Object> quota = lockScope(scope, false);
            authorize.run();
            var duplicate = jdbc.queryForList("SELECT run_id,request_sha256,actor_id FROM oa_agent_run "
                    + "WHERE tenant_id=? AND environment=? AND deployment_id=? AND idempotency_key=?",
                    scope.tenant(), scope.environment(), deploymentId, key);
            if (!duplicate.isEmpty()) {
                var previous = duplicate.getFirst();
                // An idempotency key is not a capability to read another principal's result.
                if (!actor.equals(previous.get("actor_id"))) throw new RunFailure("resource_unavailable");
                if (!fingerprint.equals(previous.get("request_sha256"))) throw new RunFailure("idempotency_conflict");
                return new Accepted((String) previous.get("run_id"), true);
            }
            requireActive(quota);
            Map<String, Object> deployment = deployment(scope, deploymentId);
            long queued = count("SELECT COUNT(*) FROM oa_agent_run WHERE tenant_id=? AND environment=? AND state='queued'",
                    scope.tenant(), scope.environment());
            if (queued >= number(quota, "max_queued_runs")) throw new RunFailure("queue_full");
            Instant now = now();
            Instant retention = now.plusSeconds(number(quota, "audit_retention_days") * 86400L);
            String id = UUID.randomUUID().toString();
            jdbc.update("""
                    INSERT INTO oa_agent_run(tenant_id,environment,run_id,deployment_id,actor_id,idempotency_key,
                        request_sha256,input_json,snapshot_json,snapshot_sha256,trace_id,deadline_at,retain_until)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """, scope.tenant(), scope.environment(), id, deploymentId, actor, key, fingerprint, body,
                    deployment.get("snapshot_json"), deployment.get("snapshot_sha256"), UUID.randomUUID().toString(),
                    Timestamp.from(now.plusSeconds(timeoutSeconds)), Timestamp.from(retention));
            jdbc.update("""
                    INSERT INTO oa_skill_release_reference(tenant_id,release_id,reference_type,reference_id,retain_until)
                    SELECT tenant_id,release_id,'run',?,? FROM oa_agent_deployment_skill
                    WHERE tenant_id=? AND environment=? AND deployment_id=?
                    """, id, Timestamp.from(retention), scope.tenant(), scope.environment(), deploymentId);
            stateEvent(scope, id, RunState.QUEUED);
            outbox(scope, id, "start");
            audit(scope, actor, "run.created", id);
            return new Accepted(id, false);
        });
        LOG.info("Run admitted run_id={} reused={}", result.runId(), result.reused());
        return result;
    }

    /** Atomic execution admission. Duplicated deliveries never start a second attempt. */
    public Optional<Lease> claim(Scope scope, String runId, String workerId) {
        uuid(runId);
        if (workerId == null || !workerId.matches("[A-Za-z0-9_.:-]{1,128}")) throw new RunFailure("invalid_worker");
        return tx(() -> {
            var quota = lockScope(scope, false);
            var run = run(scope, runId);
            if (state(run) != RunState.QUEUED) return Optional.empty();
            if (!instant(run, "deadline_at").isAfter(now())) {
                transition(scope, run, RunState.TIMED_OUT, "deadline_exceeded");
                return Optional.empty();
            }
            // Suspended tenants remain queued; no new model or tool operation may start.
            if (!"active".equals(quota.get("status"))) return Optional.empty();
            try { deployment(scope, (String) run.get("deployment_id")); }
            catch (RunFailure unavailable) {
                transition(scope, run, RunState.FAILED, "configuration_unavailable");
                return Optional.empty();
            }
            long active = count("SELECT COUNT(*) FROM oa_run_attempt WHERE tenant_id=? AND environment=? AND lease_state<>'released'",
                    scope.tenant(), scope.environment());
            if (active >= number(quota, "max_concurrent_runs")) return Optional.empty();
            int attempt = Math.toIntExact(number(run, "attempt_no") + 1);
            long fence = number(run, "revision") + 1;
            String leaseId = UUID.randomUUID().toString();
            Instant heartbeat = min(now().plusSeconds(30), instant(run, "deadline_at"));
            jdbc.update("""
                    INSERT INTO oa_run_attempt(tenant_id,environment,run_id,attempt_no,lease_id,worker_id,fencing_token,heartbeat_until)
                    VALUES(?,?,?,?,?,?,?,?)
                    """, scope.tenant(), scope.environment(), runId, attempt, leaseId, workerId, fence, Timestamp.from(heartbeat));
            transition(scope, run, RunState.RUNNING, null);
            jdbc.update("UPDATE oa_agent_run SET attempt_no=?,started_at=CURRENT_TIMESTAMP(3) WHERE tenant_id=? AND environment=? AND run_id=?",
                    attempt, scope.tenant(), scope.environment(), runId);
            return Optional.of(new Lease(scope, runId, attempt, leaseId, fence, workerId));
        });
    }

    public Map<String, Object> get(Scope scope, String runId) {
        uuid(runId);
        var rows = jdbc.queryForList("SELECT " + RUN_COLUMNS + " FROM oa_agent_run WHERE tenant_id=? AND environment=? AND run_id=?",
                scope.tenant(), scope.environment(), runId);
        if (rows.isEmpty()) throw new RunFailure("resource_unavailable");
        return rows.getFirst();
    }

    public Map<String, Object> cancel(Scope scope, String runId, String actor, Runnable authorize) {
        uuid(runId);
        return tx(() -> {
            lockScope(scope, false);
            authorize.run();
            var run = run(scope, runId);
            RunState before = state(run);
            if (before.terminal() || before == RunState.CANCEL_REQUESTED) return get(scope, runId);
            transition(scope, run, RunState.CANCEL_REQUESTED, null);
            if (before == RunState.QUEUED) transition(scope, run(scope, runId), RunState.CANCELLED, null);
            else outbox(scope, runId, "cancel");
            audit(scope, actor, "run.cancel.requested", runId);
            return get(scope, runId);
        });
    }

    /** Trusted reconciliation of an activity that failed before admission; never fences a started attempt. */
    Map<String, Object> abandonQueued(Scope scope, String runId, String supervisor) {
        uuid(runId);
        if (supervisor == null || !supervisor.matches("[A-Za-z0-9_.:-]{1,128}")) throw new RunFailure("invalid_worker");
        return tx(() -> {
            lockScope(scope, false);
            var run = run(scope, runId);
            if (state(run) == RunState.QUEUED && number(run, "attempt_no") == 0) {
                boolean expired = !instant(run, "deadline_at").isAfter(now());
                transition(scope, run, expired ? RunState.TIMED_OUT : RunState.FAILED,
                        expired ? "deadline_exceeded" : "outcome_unknown");
                audit(scope, supervisor, "run.unstarted.failed", runId);
            }
            return get(scope, runId);
        });
    }

    /** A false heartbeat fences further operations. An expired lease can never be renewed. */
    public boolean heartbeat(Lease lease) {
        return tx(() -> {
            var quota = lockScope(lease.scope(), false);
            var run = run(lease.scope(), lease.runId());
            var attempt = attempt(lease);
            Instant now = now();
            if (!"active".equals(quota.get("status")) || !permit(run, attempt, now)) return false;
            jdbc.update("UPDATE oa_run_attempt SET heartbeat_until=? WHERE lease_id=?",
                    Timestamp.from(min(now.plusSeconds(30), instant(run, "deadline_at"))), lease.leaseId());
            return true;
        });
    }

    /** Trusted runtime broker only: revalidate fencing before reading any scoped secret/artifact metadata. */
    public <T> T withPermit(Lease lease, Supplier<T> read) {
        return tx(() -> {
            var quota = lockScope(lease.scope(), false);
            var run = run(lease.scope(), lease.runId());
            var attempt = attempt(lease);
            if (!"active".equals(quota.get("status")) || !permit(run, attempt, now())) throw new RunFailure("lease_fenced");
            deployment(lease.scope(), (String) run.get("deployment_id"));
            return read.get();
        });
    }

    /** Completion and cleanup are deliberately separate. Terminal state does not free a slot. */
    public Map<String, Object> finish(Lease lease, RunState requested, String errorCode) {
        if (requested == null || !Set.of(RunState.SUCCEEDED, RunState.FAILED, RunState.TIMED_OUT).contains(requested)
                || (errorCode != null && !WORKER_ERRORS.contains(errorCode))) throw new RunFailure("invalid_completion");
        return tx(() -> {
            var quota=lockScope(lease.scope(), false);
            var run = run(lease.scope(), lease.runId());
            var attempt = attempt(lease);
            if (state(run).terminal()) return get(lease.scope(), lease.runId());
            if (number(run, "attempt_no") != lease.attempt() || !"held".equals(attempt.get("lease_state")))
                throw new RunFailure("lease_fenced");
            Instant now = now();
            RunState next = requested;
            String code = errorCode;
            if (state(run) == RunState.CANCEL_REQUESTED) { next = RunState.CANCELLED; code = null; }
            else if (!instant(run, "deadline_at").isAfter(now)) { next = RunState.TIMED_OUT; code = "deadline_exceeded"; }
            else if (!instant(attempt, "heartbeat_until").isAfter(now)) { next = RunState.FAILED; code = "outcome_unknown"; }
            if(next == RunState.SUCCEEDED) {
                // Revocation may race the final model response. Recheck before
                // recording success so a revoked run cannot authorize promotion.
                try {
                    requireActive(quota);
                    deployment(lease.scope(),(String)run.get("deployment_id"));
                } catch(RunFailure revoked) {
                    next=RunState.FAILED;code="permission_revoked";
                }
            }
            if (next == RunState.SUCCEEDED) code = null;
            transition(lease.scope(), run, next, code);
            jdbc.update("UPDATE oa_run_attempt SET lease_state='cleanup_pending' WHERE lease_id=? AND lease_state='held'", lease.leaseId());
            return get(lease.scope(), lease.runId());
        });
    }

    /** Internal journal callbacks may arrive after cancellation. Ownership still has to match exactly. */
    <T> T withAttempt(Lease lease, Supplier<T> operation) {
        return tx(() -> {
            lockScope(lease.scope(), false);
            run(lease.scope(), lease.runId());
            attempt(lease);
            return operation.get();
        });
    }

    /** Trusted reconciler invokes only AFTER sandbox deletion and worker termination are verified. */
    public void resourcesReleased(Lease lease) {
        tx(() -> {
            lockScope(lease.scope(), false);
            var run = run(lease.scope(), lease.runId());
            attempt(lease);
            if (!state(run).terminal()) throw new RunFailure("cleanup_before_terminal");
            if (count("SELECT COUNT(*) FROM oa_run_sandbox WHERE lease_id=? AND phase<>'released'", lease.leaseId()) != 0)
                throw new RunFailure("sandbox_cleanup_pending");
            jdbc.update("UPDATE oa_run_attempt SET lease_state='released',released_at=CURRENT_TIMESTAMP(3) "
                    + "WHERE lease_id=? AND lease_state='cleanup_pending'", lease.leaseId());
            return null;
        });
    }

    public long append(Lease lease, String eventKey, String type, JsonNode payload) {
        if (eventKey == null || type == null || !eventKey.matches("[A-Za-z0-9_.:-]{1,120}")
                || !Set.of("message.delta", "tool.started", "tool.finished", "usage", "execution.notice").contains(type))
            throw new RunFailure("invalid_event");
        String body = RunJson.encode(json, payload, 16384);
        return tx(() -> {
            var quota = lockScope(lease.scope(), false);
            var run = run(lease.scope(), lease.runId());
            var attempt = attempt(lease);
            if (!"active".equals(quota.get("status")) || !permit(run, attempt, now())) throw new RunFailure("lease_fenced");
            long sequence=event(lease.scope(), lease.runId(), "worker:" + eventKey, type, body, true);
            if(type.equals("usage")) {
                for(String field:List.of("prompt_tokens","completion_tokens","total_tokens")) {
                    JsonNode value=payload.get(field);
                    if(value==null||!value.isIntegralNumber()||!value.canConvertToLong()||value.longValue()<0)
                        throw new RunFailure("invalid_usage");
                }
                if(payload.size()!=3) throw new RunFailure("invalid_usage");
                // Retrying an older event must not overwrite newer cumulative usage.
                jdbc.update("UPDATE oa_agent_run SET usage_json=? WHERE tenant_id=? AND environment=? AND run_id=? AND last_event_seq=?",
                        body,lease.scope().tenant(),lease.scope().environment(),lease.runId(),sequence);
            }
            return sequence;
        });
    }

    public EventPage events(Scope scope, String runId, long after, int limit) {
        return events(scope,runId,after,limit,()->{});
    }

    public EventPage events(Scope scope, String runId, long after, int limit,Runnable authorize) {
        if (after < 0 || limit < 1 || limit > 200) throw new RunFailure("invalid_cursor");
        return tx(() -> {
            // Serialize with append/retention so the high-water mark and page form one view.
            lockScope(scope, false);
            authorize.run();
            var run = run(scope, runId);
            long latest = number(run, "last_event_seq");
            long expired = count("SELECT COALESCE(MAX(sequence_no),0) FROM oa_run_event "
                    + "WHERE tenant_id=? AND environment=? AND run_id=? AND retain_until<=CURRENT_TIMESTAMP(3)",
                    scope.tenant(), scope.environment(), runId);
            if (after < Math.max(expired, number(run, "event_floor"))) throw new RunFailure("event_history_expired");
            if (after > latest) throw new RunFailure("invalid_cursor");
            var page = jdbc.queryForList("SELECT sequence_no,event_type,payload_json,created_at FROM oa_run_event "
                    + "WHERE tenant_id=? AND environment=? AND run_id=? AND sequence_no>? ORDER BY sequence_no LIMIT ?",
                    scope.tenant(), scope.environment(), runId, after, limit);
            return new EventPage(page, latest, state(run).terminal());
        });
    }

    private Map<String, Object> lockScope(Scope scope, boolean active) {
        var tenants = jdbc.queryForList("SELECT status FROM oa_tenant WHERE tenant_id=? FOR UPDATE", scope.tenant());
        if (tenants.isEmpty()) throw new RunFailure("resource_unavailable");
        var rows = jdbc.queryForList("SELECT * FROM oa_tenant_environment WHERE tenant_id=? AND environment=? FOR UPDATE", scope.tenant(), scope.environment());
        if (rows.isEmpty()) throw new RunFailure("resource_unavailable");
        var quota = rows.getFirst();
        quota.put("status", tenants.getFirst().get("status"));
        if (active) requireActive(quota);
        return quota;
    }
    private void requireActive(Map<String, Object> quota) {
        if (!"active".equals(quota.get("status"))) throw new RunFailure("tenant_inactive");
    }
    private Map<String, Object> deployment(Scope scope, String id) {
        var rows = jdbc.queryForList("""
                SELECT d.* FROM oa_agent_deployment d JOIN oa_model_connection c
                ON c.tenant_id=d.tenant_id AND c.environment=d.environment AND c.connection_id=d.model_connection_id
                JOIN oa_model_connection_version v ON v.tenant_id=d.tenant_id AND v.environment=d.environment
                AND v.connection_id=d.model_connection_id AND v.version=d.model_version
                WHERE d.tenant_id=? AND d.environment=? AND d.deployment_id=? AND d.state IN ('test','published')
                AND c.enabled=true AND v.revoked=false
                AND NOT EXISTS (SELECT 1 FROM oa_agent_deployment_skill b JOIN oa_skill_release s
                    ON s.tenant_id=b.tenant_id AND s.release_id=b.release_id
                    WHERE b.tenant_id=d.tenant_id AND b.environment=d.environment AND b.deployment_id=d.deployment_id
                    AND (s.status<>'ready' OR s.manifest_sha256<>b.manifest_sha256))
                """, scope.tenant(), scope.environment(), id);
        if (rows.isEmpty()) throw new RunFailure("deployment_unavailable");
        var row = rows.getFirst();
        if (!RunJson.hash((String) row.get("snapshot_json")).equals(row.get("snapshot_sha256"))) throw new RunFailure("deployment_unavailable");
        return row;
    }
    private Map<String, Object> run(Scope scope, String id) {
        var rows = jdbc.queryForList("SELECT * FROM oa_agent_run WHERE tenant_id=? AND environment=? AND run_id=? FOR UPDATE",
                scope.tenant(), scope.environment(), id);
        if (rows.isEmpty()) throw new RunFailure("resource_unavailable");
        return rows.getFirst();
    }
    private Map<String, Object> attempt(Lease lease) {
        var rows = jdbc.queryForList("SELECT * FROM oa_run_attempt WHERE tenant_id=? AND environment=? AND run_id=? "
                + "AND attempt_no=? AND lease_id=? AND fencing_token=? AND worker_id=? FOR UPDATE",
                lease.scope().tenant(), lease.scope().environment(), lease.runId(), lease.attempt(), lease.leaseId(), lease.fencingToken(), lease.workerId());
        if (rows.isEmpty()) throw new RunFailure("lease_fenced");
        return rows.getFirst();
    }
    private boolean permit(Map<String, Object> run, Map<String, Object> attempt, Instant now) {
        return state(run) == RunState.RUNNING && "held".equals(attempt.get("lease_state"))
                && number(run, "attempt_no") == number(attempt, "attempt_no")
                && instant(run, "deadline_at").isAfter(now) && instant(attempt, "heartbeat_until").isAfter(now);
    }
    private void transition(Scope scope, Map<String, Object> run, RunState next, String error) {
        RunState before = state(run);
        if (!before.canTransitionTo(next)) throw new RunFailure("invalid_transition");
        if (before == next) return;
        String id = (String) run.get("run_id");
        int changed = jdbc.update("UPDATE oa_agent_run SET state=?,revision=revision+1,error_code=?,finished_at=? "
                + "WHERE tenant_id=? AND environment=? AND run_id=? AND revision=? AND state=?",
                next.value(), error, next.terminal() ? Timestamp.from(now()) : null,
                scope.tenant(), scope.environment(), id, number(run, "revision"), before.value());
        if (changed != 1) throw new RunFailure("state_conflict");
        stateEvent(scope, id, next);
    }
    private void stateEvent(Scope scope, String id, RunState state) {
        event(scope, id, "state:" + state.value(), "run.state", "{\"state\":\"" + state.value() + "\"}", false);
    }
    private long event(Scope scope, String id, String key, String type, String body, boolean bounded) {
        String hash = RunJson.hash(type + "\n" + body);
        var previous = jdbc.queryForList("SELECT sequence_no,payload_sha256 FROM oa_run_event WHERE tenant_id=? AND environment=? AND run_id=? AND event_key=?",
                scope.tenant(), scope.environment(), id, key);
        if (!previous.isEmpty()) {
            if (!hash.equals(previous.getFirst().get("payload_sha256"))) throw new RunFailure("event_conflict");
            return number(previous.getFirst(), "sequence_no");
        }
        var run = run(scope, id);
        int bytes = body.getBytes(StandardCharsets.UTF_8).length;
        long sequence = number(run, "last_event_seq") + 1;
        if (bounded && (sequence > 4096 || number(run, "event_bytes") + bytes > 1048576)) throw new RunFailure("event_limit");
        jdbc.update("INSERT INTO oa_run_event(tenant_id,environment,run_id,sequence_no,event_key,event_type,payload_json,payload_sha256,retain_until) VALUES(?,?,?,?,?,?,?,?,?)",
                scope.tenant(), scope.environment(), id, sequence, key, type, body, hash, run.get("retain_until"));
        jdbc.update("UPDATE oa_agent_run SET last_event_seq=?,event_bytes=event_bytes+? WHERE tenant_id=? AND environment=? AND run_id=?",
                sequence, bytes, scope.tenant(), scope.environment(), id);
        return sequence;
    }
    private void outbox(Scope scope, String id, String operation) {
        jdbc.update("INSERT INTO oa_run_outbox(tenant_id,environment,run_id,operation) VALUES(?,?,?,?)",
                scope.tenant(), scope.environment(), id, operation);
    }
    private void audit(Scope scope, String actor, String action, String id) {
        jdbc.update("""
                INSERT INTO oa_tenant_audit(tenant_id,environment,actor_uid,action,subject_id,retain_until)
                SELECT tenant_id,environment,?,?,?,TIMESTAMPADD(DAY,audit_retention_days,CURRENT_TIMESTAMP(3))
                FROM oa_tenant_environment WHERE tenant_id=? AND environment=?
                """, actor, action, id, scope.tenant(), scope.environment());
    }
    private <T> T tx(Supplier<T> operation) { return transaction.execute(status -> operation.get()); }
    private long count(String sql, Object... args) { return jdbc.queryForObject(sql, Long.class, args); }
    private Instant now() { return jdbc.queryForObject("SELECT CURRENT_TIMESTAMP(3)", Timestamp.class).toInstant(); }
    private static long number(Map<String, Object> row, String key) { return ((Number) row.get(key)).longValue(); }
    private static Instant instant(Map<String, Object> row, String key) { return ((Timestamp) row.get(key)).toInstant(); }
    private static RunState state(Map<String, Object> row) { return RunState.parse((String) row.get("state")); }
    private static Instant min(Instant first, Instant second) { return first.isBefore(second) ? first : second; }
    private static void uuid(String value) {
        try { if (!UUID.fromString(value).toString().equals(value)) throw new IllegalArgumentException(); }
        catch (RuntimeException exception) { throw new RunFailure("invalid_identifier"); }
    }
}
