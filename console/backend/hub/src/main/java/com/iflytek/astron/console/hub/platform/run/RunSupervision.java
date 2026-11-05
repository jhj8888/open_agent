package com.iflytek.astron.console.hub.platform.run;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.iflytek.astron.console.hub.config.PlatformInternalSecurityConfig.ServiceKey;
import java.sql.Timestamp;
import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Set;
import java.util.UUID;
import java.util.function.Supplier;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.core.env.Environment;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.TransactionDefinition;
import org.springframework.transaction.support.TransactionTemplate;

/** Independent supervisor boundary. Never supplied to worker transports or tenant code. */
@Component
public class RunSupervision {
    private static final Logger LOG = LoggerFactory.getLogger(RunSupervision.class);
    private static final String SNAPSHOT = """
            SELECT s.*,a.tenant_id,a.environment,a.run_id,a.attempt_no,a.worker_id,a.fencing_token,
                a.heartbeat_until,a.lease_state,r.state AS run_state,r.deadline_at,
                b.phase AS sandbox_phase,b.sandbox_id,b.isolation_profile,b.runtime_image
            FROM oa_run_supervision s JOIN oa_run_attempt a ON a.lease_id=s.lease_id
            JOIN oa_agent_run r ON r.run_id=a.run_id
            LEFT JOIN oa_run_sandbox b ON b.lease_id=s.lease_id
            """;
    private final RunLedger ledger;
    private final RunSandboxJournal journal;
    private final JdbcTemplate jdbc;
    private final ObjectMapper json;
    private final TransactionTemplate transaction;
    private final Map<String, String> workers;

    public RunSupervision(RunLedger ledger, RunSandboxJournal journal, JdbcTemplate jdbc,
            PlatformTransactionManager manager, ObjectMapper json, Environment environment) {
        this.ledger = ledger; this.journal = journal; this.jdbc = jdbc; this.json = json;
        transaction = new TransactionTemplate(manager);
        transaction.setIsolationLevel(TransactionDefinition.ISOLATION_READ_COMMITTED);
        transaction.setTimeout(15);
        try {
            Map<String, String> configured = json.readValue(environment.getProperty("PLATFORM_RECONCILER_WORKERS", "{}"), new TypeReference<>() {});
            Map<String, ServiceKey> keys = json.readValue(environment.getProperty("PLATFORM_SERVICE_KEYS", "{}"), new TypeReference<>() {});
            if (configured == null || keys == null || configured.size() > 100) throw new IllegalArgumentException();
            for (var entry : configured.entrySet()) {
                ServiceKey supervisor = keys.get(entry.getKey()), worker = keys.get(entry.getValue());
                if (!identity(entry.getKey()) || !identity(entry.getValue()) || supervisor == null || worker == null
                        || !"reconciler".equals(supervisor.role()) || !"worker".equals(worker.role())
                        || supervisor.secret() == null || supervisor.secret().equals(worker.secret())) throw new IllegalArgumentException();
            }
            workers = Map.copyOf(configured);
        } catch (Exception invalid) { throw new IllegalStateException("Invalid platform supervisor bindings"); }
    }

    public record Snapshot(RunLedger.Lease lease, String supervisorId, String bootId, String unitName,
            String invocationId, String phase, String state, String leaseState, Instant heartbeatUntil, Instant deadlineAt,
            String sandboxPhase, String sandboxId, String isolationProfile, String runtimeImage) {}
    public record Claim(RunLedger.Lease lease, String state, Snapshot execution) {}
    public record Lookup(String state, Snapshot execution) {}
    public record Proof(String proofId, String kind, String bootId, String unitName, String invocationId,
            String sandboxId, String evidenceSha256, boolean workerStopped, boolean cgroupEmpty,
            boolean sandboxAbsent, boolean creationSettled) {}

    /** Ledger admission and supervisor binding commit together; no physical launch in this transaction. */
    public Claim claim(RunLedger.Scope scope, String runId, String supervisor, String bootId) {
        String worker = worker(supervisor); uuid(bootId);
        return transaction.execute(status -> {
            var lease = ledger.claim(scope, runId, worker);
            if (lease.isEmpty()) return new Claim(null, (String) ledger.get(scope, runId).get("state"), null);
            var value = lease.orElseThrow();
            String unit = "oa-exec-" + value.leaseId().replace("-", "") + ".service";
            jdbc.update("INSERT INTO oa_run_supervision(lease_id,supervisor_id,boot_id,unit_name) VALUES(?,?,?,?)",
                    value.leaseId(), supervisor, bootId, unit);
            LOG.info("Execution bound run_id={} lease_id={} supervisor={}", runId, value.leaseId(), supervisor);
            return new Claim(value, "running", snapshot(row(value.leaseId(), supervisor)));
        });
    }

    /** Includes released attempts; absence from the pending scan is not a cleanup acknowledgement. */
    public Lookup lookup(RunLedger.Scope scope, String runId, String supervisor) {
        worker(supervisor);
        var run = ledger.get(scope, runId);
        var rows = jdbc.queryForList(SNAPSHOT + " WHERE a.tenant_id=? AND a.environment=? AND a.run_id=? ORDER BY a.attempt_no DESC LIMIT 1",
                scope.tenant(), scope.environment(), runId);
        if (!rows.isEmpty()) {
            var result = snapshot(rows.getFirst());
            if (!supervisor.equals(result.supervisorId())) throw new RunFailure("resource_unavailable");
            return new Lookup(result.state(), result);
        }
        if (((Number)run.get("attempt_no")).intValue() != 0) throw new RunFailure("resource_unavailable");
        return new Lookup((String)run.get("state"), null);
    }

    public Lookup abandon(RunLedger.Scope scope, String runId, String supervisor) {
        worker(supervisor);
        ledger.abandonQueued(scope, runId, supervisor);
        return lookup(scope, runId, supervisor);
    }

    /** A lost begin response MUST NOT be retried as permission to launch. */
    public Snapshot begin(String leaseId, String supervisor) {
        var lease = ownedLease(leaseId, supervisor);
        return ledger.withPermit(lease, () -> {
            if (jdbc.update("UPDATE oa_run_supervision SET phase='launching',launch_started_at=CURRENT_TIMESTAMP(3) "
                    + "WHERE lease_id=? AND supervisor_id=? AND phase='prepared'", leaseId, supervisor) != 1)
                throw new RunFailure("supervision_conflict");
            return snapshot(row(leaseId, supervisor));
        });
    }

    /** Identity observation may arrive after cancellation, but cannot change an observed invocation. */
    public Snapshot observed(String leaseId, String supervisor, String invocationId) {
        invocation(invocationId);
        return locked(leaseId, supervisor, () -> {
            var row = row(leaseId, supervisor);
            if ("prepared".equals(row.get("phase")) || "released".equals(row.get("phase"))
                    || (row.get("invocation_id") != null && !invocationId.equals(row.get("invocation_id"))))
                throw new RunFailure("supervision_conflict");
            jdbc.update("UPDATE oa_run_supervision SET phase='observed',invocation_id=?,observed_at=COALESCE(observed_at,CURRENT_TIMESTAMP(3)) WHERE lease_id=?",
                    invocationId, leaseId);
            return snapshot(row(leaseId, supervisor));
        });
    }

    public Snapshot inspect(String leaseId, String supervisor) {
        return locked(leaseId, supervisor, () -> snapshot(row(leaseId, supervisor)));
    }

    /** Keyset pagination prevents old unknown attempts from hiding newer cleanup work. */
    public List<Snapshot> pending(String supervisor, String after, int limit) {
        worker(supervisor);
        if (after != null) uuid(after);
        if (limit < 1 || limit > 100) throw new RunFailure("invalid_cursor");
        return jdbc.queryForList(SNAPSHOT + " WHERE s.supervisor_id=? AND s.phase<>'released' AND s.lease_id>? ORDER BY s.lease_id LIMIT ?",
                supervisor, after == null ? "" : after, limit).stream().map(this::snapshot).toList();
    }

    /** Fence before physical cleanup; ledger preserves cancellation, deadline and existing terminal state. */
    public Snapshot fence(String leaseId, String supervisor) {
        return locked(leaseId, supervisor, () -> {
            var row = row(leaseId, supervisor);
            ledger.finish(snapshot(row).lease(), RunState.FAILED, "outcome_unknown");
            return snapshot(row(leaseId, supervisor));
        });
    }

    /** Only after independently identifying exactly one entity by the complete lease metadata. */
    public Snapshot adoptSandbox(String leaseId, String supervisor, String sandboxId) {
        uuid(sandboxId);
        return locked(leaseId, supervisor, () -> {
            var row = row(leaseId, supervisor);
            if ("prepared".equals(row.get("phase")) || "released".equals(row.get("phase")))
                throw new RunFailure("supervision_conflict");
            journal.created(snapshot(row).lease(), sandboxId);
            return snapshot(row(leaseId, supervisor));
        });
    }

    public Snapshot release(String leaseId, String supervisor, Proof proof) {
        validate(proof);
        return locked(leaseId, supervisor, () -> {
            var row = row(leaseId, supervisor);
            var snapshot = snapshot(row);
            if ("released".equals(snapshot.phase())) {
                try {
                    if (proof.proofId().equals(row.get("proof_id"))
                            && json.valueToTree(proof).equals(json.readTree((String) row.get("proof_json")))) return snapshot;
                } catch (com.fasterxml.jackson.core.JsonProcessingException invalid) { throw new RunFailure("cleanup_proof_conflict"); }
                throw new RunFailure("cleanup_proof_conflict");
            }
            if (!RunState.parse(snapshot.state()).terminal()) throw new RunFailure("cleanup_before_terminal");
            if (!proof.unitName().equals(snapshot.unitName()) || !Objects.equals(proof.invocationId(), snapshot.invocationId()))
                throw new RunFailure("cleanup_unverified");
            boolean valid = switch (proof.kind()) {
                case "never_launched" -> "prepared".equals(snapshot.phase()) && proof.bootId().equals(snapshot.bootId());
                case "stopped_unit" -> "observed".equals(snapshot.phase()) && proof.bootId().equals(snapshot.bootId());
                case "host_reboot" -> !proof.bootId().equals(snapshot.bootId());
                default -> false;
            };
            if (!valid) throw new RunFailure("cleanup_unverified");
            // An unknown in-flight create cannot be closed by an empty inventory or a caller boolean.
            if (!Objects.equals(snapshot.sandboxId(), proof.sandboxId())
                    || (snapshot.sandboxPhase() != null && !"reserved".equals(snapshot.sandboxPhase()) && snapshot.sandboxId() == null))
                throw new RunFailure("cleanup_unverified");
            String encoded = RunJson.encode(json, json.valueToTree(proof), 4096);
            jdbc.update("UPDATE oa_run_supervision SET phase='released',released_at=CURRENT_TIMESTAMP(3),proof_id=?,proof_json=? WHERE lease_id=?",
                    proof.proofId(), encoded, leaseId);
            int verified = RunVerifiedRelease.commit(ledger, journal, jdbc, snapshot.lease(), snapshot.sandboxPhase() != null, supervisor, proof.proofId());
            LOG.info("Execution release verified lease_id={} supervisor={} proof_id={} skill_evidence={}", leaseId, supervisor, proof.proofId(), verified);
            return snapshot(row(leaseId, supervisor));
        });
    }

    private RunLedger.Lease ownedLease(String leaseId, String supervisor) {
        worker(supervisor); uuid(leaseId);
        return snapshot(row(leaseId, supervisor)).lease();
    }
    private <T> T locked(String leaseId, String supervisor, Supplier<T> action) {
        return ledger.withAttempt(ownedLease(leaseId, supervisor), action);
    }
    private Map<String, Object> row(String leaseId, String supervisor) {
        var rows = jdbc.queryForList(SNAPSHOT + " WHERE s.lease_id=? AND s.supervisor_id=?", leaseId, supervisor);
        if (rows.isEmpty()) throw new RunFailure("resource_unavailable");
        return rows.getFirst();
    }
    private Snapshot snapshot(Map<String, Object> row) {
        var scope = new RunLedger.Scope((String) row.get("tenant_id"), (String) row.get("environment"));
        var lease = new RunLedger.Lease(scope, (String) row.get("run_id"), ((Number) row.get("attempt_no")).intValue(),
                (String) row.get("lease_id"), ((Number) row.get("fencing_token")).longValue(), (String) row.get("worker_id"));
        return new Snapshot(lease, (String) row.get("supervisor_id"), (String) row.get("boot_id"), (String) row.get("unit_name"),
                (String) row.get("invocation_id"), (String) row.get("phase"), (String) row.get("run_state"), (String) row.get("lease_state"),
                ((Timestamp) row.get("heartbeat_until")).toInstant(), ((Timestamp) row.get("deadline_at")).toInstant(),
                (String) row.get("sandbox_phase"), (String) row.get("sandbox_id"), (String) row.get("isolation_profile"), (String) row.get("runtime_image"));
    }
    private String worker(String supervisor) {
        String value = workers.get(supervisor);
        if (value == null) throw new RunFailure("action_forbidden");
        return value;
    }
    private static boolean identity(String value) { return value != null && value.matches("[A-Za-z0-9_.:-]{1,128}"); }
    private static void uuid(String value) {
        try { if (!UUID.fromString(value).toString().equals(value)) throw new IllegalArgumentException(); }
        catch (RuntimeException invalid) { throw new RunFailure("invalid_identifier"); }
    }
    private static void invocation(String value) {
        if (value == null || !value.matches("[a-f0-9]{32}")) throw new RunFailure("invalid_invocation");
    }
    private static void validate(Proof proof) {
        if (proof == null) throw new RunFailure("invalid_cleanup_proof");
        uuid(proof.proofId()); uuid(proof.bootId());
        if (proof.invocationId() != null) invocation(proof.invocationId());
        if (proof.sandboxId() != null) uuid(proof.sandboxId());
        if (proof.kind() == null || !Set.of("never_launched", "stopped_unit", "host_reboot").contains(proof.kind())
                || proof.unitName() == null || !proof.unitName().matches("oa-exec-[a-f0-9]{32}\\.service")
                || proof.evidenceSha256() == null || !proof.evidenceSha256().matches("[a-f0-9]{64}")
                || !proof.workerStopped() || !proof.cgroupEmpty() || !proof.sandboxAbsent() || !proof.creationSettled())
            throw new RunFailure("invalid_cleanup_proof");
    }
}
