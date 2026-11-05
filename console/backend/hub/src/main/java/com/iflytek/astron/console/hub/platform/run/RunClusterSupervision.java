package com.iflytek.astron.console.hub.platform.run;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.DeserializationFeature;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.iflytek.astron.console.hub.config.PlatformInternalSecurityConfig.ServiceKey;
import java.sql.Timestamp;
import java.time.Instant;
import java.util.HashSet;
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

/** Kubernetes lifecycle ledger. Physical observations are accepted only from the bound reconciler. */
@Component
public class RunClusterSupervision {
    private static final Logger LOG = LoggerFactory.getLogger(RunClusterSupervision.class);
    private static final String SNAPSHOT = """
            SELECT c.*,a.tenant_id,a.environment,a.run_id,a.attempt_no,a.worker_id,a.fencing_token,
                a.heartbeat_until,a.lease_state,r.state AS run_state,r.deadline_at,
                b.phase AS sandbox_phase,b.sandbox_id,b.isolation_profile,b.runtime_image
            FROM oa_run_cluster_execution c JOIN oa_run_attempt a ON a.lease_id=c.lease_id
            JOIN oa_agent_run r ON r.run_id=a.run_id
            LEFT JOIN oa_run_sandbox b ON b.lease_id=c.lease_id
            """;
    private final RunLedger ledger;
    private final RunSandboxJournal journal;
    private final JdbcTemplate jdbc;
    private final ObjectMapper json;
    private final TransactionTemplate transaction;
    private final Map<String, Target> targets;
    private final Map<String, SandboxTarget> sandboxTargets;

    public record Target(String workerId, String environment, String clusterId, String namespace,
                         String namespaceUid, String executorImage) {}
    public record Snapshot(RunLedger.Lease lease, String supervisorId, Target target, String podName,
            String podUid, String observedResourceVersion, String phase, String state, String leaseState,
            Instant heartbeatUntil, Instant deadlineAt, String sandboxPhase, String sandboxId,
            String isolationProfile, String runtimeImage, Termination termination, NotStarted notStarted, CreationRejection creationRejection,
            SandboxTarget sandboxTarget, SandboxResource sandboxResource) {}
    public record SandboxTarget(String namespace, String namespaceUid, String runtimeClass) {}
    /** First independent observation only; resource versions are not mutable status checkpoints. */
    public record SandboxResource(String sandboxId, String workloadUid, String workloadResourceVersion,
            String podUid, String podResourceVersion, String evidenceSha256) {}
    public record Claim(RunLedger.Lease lease, String state, Snapshot execution) {}
    public record Lookup(String state, Snapshot execution) {}
    public record Observation(String podUid, String resourceVersion) {}
    /** Only a known executor container with a kubelet termination status can establish this checkpoint. */
    public record Termination(String id, String podUid, String resourceVersion, String nodeName, String nodeUid,
            String podPhase, String containerName, String containerId, Integer exitCode,
            Instant startedAt, Instant finishedAt, String evidenceSha256) {}
    public record NotStarted(String id, String podUid, String resourceVersion, Instant deletionTimestamp,
            String schedulingGate, boolean nodeUnbound, boolean containerStatusesEmpty, String evidenceSha256) {}
    public record CreationRejection(String id, String auditId, String podName, String namespaceUid,
            Integer httpStatus, String reason, String requestSha256, String responseSha256) {}
    // terminationId references the kind-specific durable stop receipt, including never_started_pod.
    public record Proof(String proofId, String kind, String clusterId, String namespaceUid, String podName,
            String podUid, String terminationId, String sandboxId, String evidenceSha256,
            boolean podAbsent, boolean sandboxAbsent, boolean creationSettled) {}

    public RunClusterSupervision(RunLedger ledger, RunSandboxJournal journal, JdbcTemplate jdbc,
            PlatformTransactionManager manager, ObjectMapper json, Environment environment) {
        this.ledger = ledger; this.journal = journal; this.jdbc = jdbc; this.json = json;
        transaction = new TransactionTemplate(manager);
        transaction.setIsolationLevel(TransactionDefinition.ISOLATION_READ_COMMITTED);
        transaction.setTimeout(15);
        try {
            Map<String, Target> configured = json.readerFor(new TypeReference<Map<String, Target>>() {})
                    .with(DeserializationFeature.FAIL_ON_UNKNOWN_PROPERTIES)
                    .readValue(environment.getProperty("PLATFORM_CLUSTER_RECONCILERS", "{}"));
            Map<String, String> hosts = json.readValue(environment.getProperty("PLATFORM_RECONCILER_WORKERS", "{}"), new TypeReference<>() {});
            Map<String, ServiceKey> keys = json.readValue(environment.getProperty("PLATFORM_SERVICE_KEYS", "{}"), new TypeReference<>() {});
            if (configured == null || hosts == null || keys == null || configured.size() > 100) throw new IllegalArgumentException();
            var workers = new HashSet<>(hosts.values());
            var namespaces = new java.util.HashMap<String, String>();
            for (var entry : configured.entrySet()) {
                Target target = entry.getValue();
                if (target == null || !identity(entry.getKey()) || !identity(target.workerId())
                        || hosts.containsKey(entry.getKey()) || !workers.add(target.workerId())
                        || !Set.of("test", "prod").contains(target.environment()) || !dns(target.namespace(), 63) || target.namespace().contains(".")
                        || target.executorImage() == null || target.executorImage().length() > 512
                        || !target.executorImage().matches("[a-z0-9][a-z0-9.:/_-]*@sha256:[a-f0-9]{64}")) throw new IllegalArgumentException();
                uuid(target.clusterId()); uuid(target.namespaceUid());
                String old = namespaces.putIfAbsent(target.clusterId() + "/" + target.namespaceUid(), target.environment());
                if (old != null && !old.equals(target.environment())) throw new IllegalArgumentException();
                var supervisor = keys.get(entry.getKey()); var worker = keys.get(target.workerId());
                if (supervisor == null || worker == null || !"reconciler".equals(supervisor.role()) || !"worker".equals(worker.role())
                        || supervisor.secret() == null || supervisor.secret().equals(worker.secret())) throw new IllegalArgumentException();
            }
            targets = Map.copyOf(configured);
            Map<String, SandboxTarget> sandboxes = json.readerFor(new TypeReference<Map<String, SandboxTarget>>() {})
                    .with(DeserializationFeature.FAIL_ON_UNKNOWN_PROPERTIES)
                    .readValue(environment.getProperty("PLATFORM_CLUSTER_SANDBOXES", "{}"));
            if (sandboxes == null || sandboxes.size() > 100) throw new IllegalArgumentException();
            var sandboxNamespaces = new java.util.HashMap<String, String>();
            for (var entry : sandboxes.entrySet()) {
                var execution = configured.get(entry.getKey()); var sandbox = entry.getValue();
                if (execution == null || sandbox == null || !dns(sandbox.namespace(),63) || sandbox.namespace().contains(".")
                        || !dns(sandbox.runtimeClass(),63) || sandbox.runtimeClass().contains(".")) throw new IllegalArgumentException();
                uuid(sandbox.namespaceUid());
                for (var other : configured.values()) {
                    if (other.clusterId().equals(execution.clusterId()) && (sandbox.namespace().equals(other.namespace())
                            || sandbox.namespaceUid().equals(other.namespaceUid()))) throw new IllegalArgumentException();
                }
                for (String identity : List.of(sandbox.namespace(), sandbox.namespaceUid())) {
                    String previous = sandboxNamespaces.putIfAbsent(execution.clusterId()+"/"+identity, execution.environment());
                    if (previous != null && !previous.equals(execution.environment())) throw new IllegalArgumentException();
                }
            }
            sandboxTargets = Map.copyOf(sandboxes);
        } catch (Exception invalid) { throw new IllegalStateException("Invalid platform cluster supervisor bindings"); }
    }

    public Claim claim(RunLedger.Scope scope, String runId, String supervisor) {
        Target target = scoped(supervisor, scope);
        return transaction.execute(status -> {
            var lease = ledger.claim(scope, runId, target.workerId());
            if (lease.isEmpty()) return new Claim(null, (String) ledger.get(scope, runId).get("state"), null);
            var value = lease.orElseThrow();
            String pod = "oa-exec-" + value.leaseId().replace("-", "");
            jdbc.update("""
                    INSERT INTO oa_run_cluster_execution(lease_id,supervisor_id,cluster_id,namespace_name,namespace_uid,executor_image,pod_name)
                    VALUES(?,?,?,?,?,?,?)
                    """, value.leaseId(), supervisor, target.clusterId(), target.namespace(), target.namespaceUid(), target.executorImage(), pod);
            LOG.info("Cluster execution bound run_id={} lease_id={} supervisor={}", runId, value.leaseId(), supervisor);
            return new Claim(value, "running", snapshot(row(value.leaseId(), supervisor)));
        });
    }

    public Lookup lookup(RunLedger.Scope scope, String runId, String supervisor) {
        scoped(supervisor, scope);
        var run = ledger.get(scope, runId);
        var rows = jdbc.queryForList(SNAPSHOT + " WHERE a.tenant_id=? AND a.environment=? AND a.run_id=? ORDER BY a.attempt_no DESC LIMIT 1",
                scope.tenant(), scope.environment(), runId);
        if (!rows.isEmpty()) {
            var result = snapshot(rows.getFirst());
            owned(result, supervisor);
            return new Lookup(result.state(), result);
        }
        if (((Number) run.get("attempt_no")).intValue() != 0) throw new RunFailure("resource_unavailable");
        return new Lookup((String) run.get("state"), null);
    }

    public Lookup abandon(RunLedger.Scope scope, String runId, String supervisor) {
        scoped(supervisor, scope);
        ledger.abandonQueued(scope, runId, supervisor);
        return lookup(scope, runId, supervisor);
    }

    public Snapshot begin(String leaseId, String supervisor) {
        return ledger.withPermit(ownedLease(leaseId, supervisor), () -> {
            if (jdbc.update("UPDATE oa_run_cluster_execution SET phase='launching',launch_started_at=CURRENT_TIMESTAMP(3) "
                    + "WHERE lease_id=? AND phase='prepared'", leaseId) != 1) throw new RunFailure("cluster_execution_conflict");
            return snapshot(row(leaseId, supervisor));
        });
    }

    public Snapshot observed(String leaseId, String supervisor, Observation observation) {
        if (observation == null) throw new RunFailure("invalid_cluster_observation");
        uuid(observation.podUid()); version(observation.resourceVersion());
        return locked(leaseId, supervisor, () -> {
            var value = snapshot(row(leaseId, supervisor));
            if (value.podUid() != null && value.podUid().equals(observation.podUid())) return value;
            if (!"launching".equals(value.phase())) throw new RunFailure("cluster_execution_conflict");
            jdbc.update("UPDATE oa_run_cluster_execution SET phase='observed',pod_uid=?,observed_resource_version=?,observed_at=CURRENT_TIMESTAMP(3) WHERE lease_id=?",
                    observation.podUid(), observation.resourceVersion(), leaseId);
            return snapshot(row(leaseId, supervisor));
        });
    }

    /** Commit BEFORE removing the executor finalizer; an API 404 alone is never evidence of termination. */
    public Snapshot terminated(String leaseId, String supervisor, Termination termination) {
        validate(termination);
        return locked(leaseId, supervisor, () -> {
            var row = row(leaseId, supervisor); var value = snapshot(row);
            if (value.termination() != null) {
                if (same(termination, (String) row.get("termination_json"))) return value;
                throw new RunFailure("cluster_termination_conflict");
            }
            if (!"observed".equals(value.phase()) || !termination.podUid().equals(value.podUid()))
                throw new RunFailure("cluster_termination_unverified");
            jdbc.update("UPDATE oa_run_cluster_execution SET phase='terminated',terminated_at=CURRENT_TIMESTAMP(3),termination_id=?,termination_json=? WHERE lease_id=?",
                    termination.id(), RunJson.encode(json, json.valueToTree(termination), 4096), leaseId);
            LOG.info("Cluster termination recorded lease_id={} supervisor={} termination_id={}", leaseId, supervisor, termination.id());
            return snapshot(row(leaseId, supervisor));
        });
    }

    public Snapshot inspect(String leaseId, String supervisor) { return locked(leaseId, supervisor, () -> snapshot(row(leaseId, supervisor))); }

    /** A completed pre-storage API denial, not a timeout/404 or a guessed absence. */
    public Snapshot creationRejected(String leaseId, String supervisor, CreationRejection receipt) {
        validate(receipt);
        return locked(leaseId, supervisor, () -> {
            var row = row(leaseId, supervisor); var value = snapshot(row);
            if (value.creationRejection() != null) {
                if (same(receipt, (String) row.get("creation_rejection_json"))) return value;
                throw new RunFailure("cluster_creation_receipt_conflict");
            }
            if (!"launching".equals(value.phase()) || value.podUid() != null
                    || !receipt.podName().equals(value.podName()) || !receipt.namespaceUid().equals(value.target().namespaceUid())
                    || value.sandboxId() != null || (value.sandboxPhase() != null && !"reserved".equals(value.sandboxPhase())))
                throw new RunFailure("cluster_creation_rejection_unverified");
            ledger.finish(value.lease(), RunState.FAILED, "configuration_unavailable");
            jdbc.update("UPDATE oa_run_cluster_execution SET phase='rejected',creation_rejected_at=CURRENT_TIMESTAMP(3),creation_rejection_id=?,creation_rejection_json=? WHERE lease_id=?",
                    receipt.id(), RunJson.encode(json, json.valueToTree(receipt), 4096), leaseId);
            LOG.info("Cluster creation rejection recorded lease_id={} supervisor={} receipt_id={} status={}", leaseId, supervisor, receipt.id(), receipt.httpStatus());
            return snapshot(row(leaseId, supervisor));
        });
    }

    /** Positive API deletion barrier: never bound to a node; late binding is rejected by Kubernetes. */
    public Snapshot notStarted(String leaseId, String supervisor, NotStarted receipt) {
        validate(receipt);
        return locked(leaseId, supervisor, () -> {
            var row = row(leaseId, supervisor); var value = snapshot(row);
            if (value.notStarted() != null) {
                if (same(receipt, (String) row.get("not_started_json"))) return value;
                throw new RunFailure("cluster_stop_receipt_conflict");
            }
            if (!RunState.parse(value.state()).terminal()) throw new RunFailure("cleanup_before_terminal");
            if (!"observed".equals(value.phase()) || value.termination() != null || !receipt.podUid().equals(value.podUid())
                    || value.sandboxId() != null || (value.sandboxPhase() != null && !"reserved".equals(value.sandboxPhase())))
                throw new RunFailure("cluster_not_started_unverified");
            jdbc.update("UPDATE oa_run_cluster_execution SET phase='not_started',not_started_at=CURRENT_TIMESTAMP(3),not_started_id=?,not_started_json=? WHERE lease_id=?",
                    receipt.id(), RunJson.encode(json, json.valueToTree(receipt), 4096), leaseId);
            LOG.info("Cluster unstarted Pod recorded lease_id={} supervisor={} receipt_id={}", leaseId, supervisor, receipt.id());
            return snapshot(row(leaseId, supervisor));
        });
    }

    public List<Snapshot> pending(String supervisor, String after, int limit) {
        target(supervisor); if (after != null) uuid(after);
        if (limit < 1 || limit > 100) throw new RunFailure("invalid_cursor");
        return jdbc.queryForList(SNAPSHOT + " WHERE c.supervisor_id=? AND c.phase<>'released' AND c.lease_id>? ORDER BY c.lease_id LIMIT ?",
                supervisor, after == null ? "" : after, limit).stream().map(row -> {
                    var value = snapshot(row); owned(value, supervisor); return value;
                }).toList();
    }

    public Snapshot fence(String leaseId, String supervisor) {
        return locked(leaseId, supervisor, () -> {
            ledger.finish(snapshot(row(leaseId, supervisor)).lease(), RunState.FAILED, "outcome_unknown");
            return snapshot(row(leaseId, supervisor));
        });
    }

    public Snapshot adoptSandbox(String leaseId, String supervisor, String sandboxId) {
        uuid(sandboxId);
        return locked(leaseId, supervisor, () -> {
            var value = snapshot(row(leaseId, supervisor));
            if (value.sandboxTarget() != null) throw new RunFailure("cluster_sandbox_resource_required");
            if (value.podUid() == null || "released".equals(value.phase())) throw new RunFailure("cluster_execution_conflict");
            journal.created(value.lease(), sandboxId);
            return snapshot(row(leaseId, supervisor));
        });
    }

    /** Preparation is permitted only while this exact executor lease can still act. */
    public Snapshot prepareSandbox(String leaseId, String supervisor, String image) {
        var expected = sandboxTargets.get(supervisor);
        if (expected == null) throw new RunFailure("cluster_sandbox_unconfigured");
        return ledger.withPermit(ownedLease(leaseId, supervisor), () -> {
            var value = snapshot(row(leaseId, supervisor));
            if (!"observed".equals(value.phase()) || value.podUid() == null || value.sandboxResource() != null)
                throw new RunFailure("cluster_sandbox_state_conflict");
            journal.reserve(value.lease(), "kata", image);
            if (value.sandboxTarget() != null) {
                if (!expected.equals(value.sandboxTarget())) throw new RunFailure("cluster_sandbox_binding_changed");
                return snapshot(row(leaseId, supervisor));
            }
            jdbc.update("UPDATE oa_run_cluster_execution SET sandbox_target_json=? WHERE lease_id=?",
                    RunJson.encode(json,json.valueToTree(expected),1024),leaseId);
            LOG.info("Cluster sandbox target bound lease_id={} supervisor={} namespace_uid={}",leaseId,supervisor,expected.namespaceUid());
            return snapshot(row(leaseId, supervisor));
        });
    }

    /** Reconciler observation can arrive after cancellation; it never grants execution permission. */
    public Snapshot sandboxResource(String leaseId, String supervisor, SandboxResource resource) {
        if (resource == null) throw new RunFailure("invalid_cluster_sandbox_resource");
        uuid(resource.sandboxId()); uuid(resource.workloadUid()); uuid(resource.podUid());
        version(resource.workloadResourceVersion()); version(resource.podResourceVersion());
        if (!hash(resource.evidenceSha256())) throw new RunFailure("invalid_cluster_sandbox_resource");
        return locked(leaseId,supervisor,() -> {
            var value = snapshot(row(leaseId,supervisor));
            if (value.sandboxTarget() == null || !value.sandboxTarget().equals(sandboxTargets.get(supervisor)))
                throw new RunFailure("cluster_sandbox_binding_changed");
            if (value.sandboxResource() != null) {
                if (resource.equals(value.sandboxResource())) return value;
                throw new RunFailure("cluster_sandbox_resource_conflict");
            }
            if (value.podUid() == null || !Set.of("observed","terminated").contains(value.phase())
                    || value.podUid().equals(resource.podUid()) || value.podUid().equals(resource.workloadUid())
                    || resource.podUid().equals(resource.workloadUid())
                    || !"kata".equals(value.isolationProfile())
                    || value.sandboxPhase() == null || !Set.of("creating","created","cleanup_pending").contains(value.sandboxPhase()))
                throw new RunFailure("cluster_sandbox_state_conflict");
            journal.created(value.lease(),resource.sandboxId());
            jdbc.update("UPDATE oa_run_cluster_execution SET sandbox_resource_json=?,sandbox_workload_uid=?,sandbox_pod_uid=?,sandbox_bound_at=CURRENT_TIMESTAMP(3) WHERE lease_id=?",
                    RunJson.encode(json,json.valueToTree(resource),2048),resource.workloadUid(),resource.podUid(),leaseId);
            LOG.info("Cluster sandbox resource bound lease_id={} supervisor={} workload_uid={} pod_uid={}",
                    leaseId,supervisor,resource.workloadUid(),resource.podUid());
            return snapshot(row(leaseId,supervisor));
        });
    }

    public Snapshot release(String leaseId, String supervisor, Proof proof) {
        validate(proof);
        return locked(leaseId, supervisor, () -> {
            var row = row(leaseId, supervisor); var value = snapshot(row);
            if ("released".equals(value.phase())) {
                if (proof.proofId().equals(row.get("proof_id")) && same(proof, (String) row.get("proof_json"))) return value;
                throw new RunFailure("cleanup_proof_conflict");
            }
            if (!RunState.parse(value.state()).terminal()) throw new RunFailure("cleanup_before_terminal");
            // UID registration is not physical termination. The independent Kata
            // stop/settlement receipt must be connected before this can release.
            if (value.sandboxTarget() != null && (value.sandboxId() != null
                    || (value.sandboxPhase() != null && !"reserved".equals(value.sandboxPhase()))))
                throw new RunFailure("cluster_sandbox_cleanup_unavailable");
            if (!proof.clusterId().equals(value.target().clusterId()) || !proof.namespaceUid().equals(value.target().namespaceUid())
                    || !proof.podName().equals(value.podName()) || !Objects.equals(proof.podUid(), value.podUid()))
                throw new RunFailure("cleanup_unverified");
            boolean valid = switch (proof.kind()) {
                case "never_launched" -> "prepared".equals(value.phase()) && proof.podUid() == null && proof.terminationId() == null;
                case "terminated_pod" -> "terminated".equals(value.phase()) && value.termination() != null
                        && value.termination().id().equals(proof.terminationId());
                case "never_started_pod" -> "not_started".equals(value.phase()) && value.notStarted() != null
                        && value.notStarted().id().equals(proof.terminationId());
                case "create_rejected" -> "rejected".equals(value.phase()) && value.creationRejection() != null
                        && value.podUid() == null && value.creationRejection().id().equals(proof.terminationId());
                default -> false;
            };
            if (!valid || !Objects.equals(value.sandboxId(), proof.sandboxId())
                    || (value.sandboxPhase() != null && !"reserved".equals(value.sandboxPhase()) && value.sandboxId() == null))
                throw new RunFailure("cleanup_unverified");
            jdbc.update("UPDATE oa_run_cluster_execution SET phase='released',released_at=CURRENT_TIMESTAMP(3),proof_id=?,proof_json=? WHERE lease_id=?",
                    proof.proofId(), RunJson.encode(json, json.valueToTree(proof), 4096), leaseId);
            int verified = RunVerifiedRelease.commit(ledger, journal, jdbc, value.lease(), value.sandboxPhase() != null, supervisor, proof.proofId());
            LOG.info("Cluster execution release verified lease_id={} supervisor={} proof_id={} skill_evidence={}", leaseId, supervisor, proof.proofId(), verified);
            return snapshot(row(leaseId, supervisor));
        });
    }

    private RunLedger.Lease ownedLease(String leaseId, String supervisor) {
        target(supervisor); uuid(leaseId); return snapshot(row(leaseId, supervisor)).lease();
    }
    private <T> T locked(String leaseId, String supervisor, Supplier<T> action) { return ledger.withAttempt(ownedLease(leaseId, supervisor), action); }
    private Map<String, Object> row(String leaseId, String supervisor) {
        var rows = jdbc.queryForList(SNAPSHOT + " WHERE c.lease_id=? AND c.supervisor_id=?", leaseId, supervisor);
        if (rows.isEmpty()) throw new RunFailure("resource_unavailable");
        owned(snapshot(rows.getFirst()), supervisor);
        return rows.getFirst();
    }
    private Snapshot snapshot(Map<String, Object> row) {
        var scope = new RunLedger.Scope((String) row.get("tenant_id"), (String) row.get("environment"));
        var lease = new RunLedger.Lease(scope, (String) row.get("run_id"), ((Number) row.get("attempt_no")).intValue(),
                (String) row.get("lease_id"), ((Number) row.get("fencing_token")).longValue(), (String) row.get("worker_id"));
        var target = new Target(lease.workerId(), scope.environment(), (String) row.get("cluster_id"), (String) row.get("namespace_name"),
                (String) row.get("namespace_uid"), (String) row.get("executor_image"));
        Termination termination = null;
        NotStarted notStarted = null;
        CreationRejection rejection = null;
        if (row.get("termination_json") != null) {
            try { termination = json.readValue((String) row.get("termination_json"), Termination.class); }
            catch (Exception invalid) { throw new RunFailure("cluster_termination_unverified"); }
        }
        if (row.get("not_started_json") != null) {
            try { notStarted = json.readValue((String) row.get("not_started_json"), NotStarted.class); }
            catch (Exception invalid) { throw new RunFailure("cluster_not_started_unverified"); }
        }
        if (row.get("creation_rejection_json") != null) {
            try { rejection = json.readValue((String) row.get("creation_rejection_json"), CreationRejection.class); }
            catch (Exception invalid) { throw new RunFailure("cluster_creation_rejection_unverified"); }
        }
        return new Snapshot(lease, (String) row.get("supervisor_id"), target, (String) row.get("pod_name"),
                (String) row.get("pod_uid"), (String) row.get("observed_resource_version"), (String) row.get("phase"),
                (String) row.get("run_state"), (String) row.get("lease_state"),
                ((Timestamp) row.get("heartbeat_until")).toInstant(), ((Timestamp) row.get("deadline_at")).toInstant(),
                (String) row.get("sandbox_phase"), (String) row.get("sandbox_id"), (String) row.get("isolation_profile"), (String) row.get("runtime_image"), termination, notStarted, rejection,
                sandboxRecord(row,"sandbox_target_json",SandboxTarget.class),sandboxRecord(row,"sandbox_resource_json",SandboxResource.class));
    }
    private <T> T sandboxRecord(Map<String,Object> row,String column,Class<T> type) {
        if (row.get(column) == null) return null;
        try { return json.readValue((String)row.get(column),type); }
        catch (Exception invalid) { throw new RunFailure("cluster_sandbox_binding_invalid"); }
    }
    private Target target(String supervisor) {
        var value = targets.get(supervisor); if (value == null) throw new RunFailure("action_forbidden"); return value;
    }
    private Target scoped(String supervisor, RunLedger.Scope scope) {
        var value = target(supervisor);
        if (scope == null || !value.environment().equals(scope.environment())) throw new RunFailure("action_forbidden");
        return value;
    }
    private void owned(Snapshot value, String supervisor) {
        if (!supervisor.equals(value.supervisorId())) throw new RunFailure("resource_unavailable");
        if (!target(supervisor).equals(value.target())) throw new RunFailure("cluster_binding_changed");
        if (value.sandboxTarget() != null && !value.sandboxTarget().equals(sandboxTargets.get(supervisor)))
            throw new RunFailure("cluster_sandbox_binding_changed");
    }
    private boolean same(Object incoming, String stored) {
        try { return incoming.equals(json.readValue(stored, incoming.getClass())); }
        catch (Exception invalid) { return false; }
    }
    private static boolean identity(String value) { return value != null && value.matches("[A-Za-z0-9_.:-]{1,128}"); }
    private static boolean dns(String value, int limit) { return value != null && value.length() <= limit && value.matches("[a-z0-9](?:[-a-z0-9]*[a-z0-9])?(?:\\.[a-z0-9](?:[-a-z0-9]*[a-z0-9])?)*"); }
    private static void uuid(String value) {
        try { if (!UUID.fromString(value).toString().equals(value)) throw new IllegalArgumentException(); }
        catch (RuntimeException invalid) { throw new RunFailure("invalid_identifier"); }
    }
    private static void version(String value) {
        if (value == null || !value.matches("[A-Za-z0-9_.:-]{1,128}")) throw new RunFailure("invalid_resource_version");
    }
    private static boolean hash(String value) { return value != null && value.matches("[a-f0-9]{64}"); }
    private static void validate(Termination value) {
        if (value == null) throw new RunFailure("invalid_cluster_termination");
        uuid(value.id()); uuid(value.podUid()); uuid(value.nodeUid()); version(value.resourceVersion());
        if (!dns(value.nodeName(), 253) || !("Succeeded".equals(value.podPhase()) || "Failed".equals(value.podPhase()))
                || !"executor".equals(value.containerName()) || value.containerId() == null
                || !value.containerId().matches("[a-z][a-z0-9+.-]{0,20}://[A-Za-z0-9_.:-]{1,256}")
                || value.exitCode() == null || value.exitCode() < 0 || value.exitCode() > 255 || ("Succeeded".equals(value.podPhase()) && value.exitCode() != 0)
                || value.startedAt() == null || value.finishedAt() == null || value.finishedAt().isBefore(value.startedAt())
                || !hash(value.evidenceSha256())) throw new RunFailure("invalid_cluster_termination");
    }
    private static void validate(Proof value) {
        if (value == null) throw new RunFailure("invalid_cleanup_proof");
        uuid(value.proofId()); uuid(value.clusterId()); uuid(value.namespaceUid());
        if (value.podUid() != null) uuid(value.podUid());
        if (value.terminationId() != null) uuid(value.terminationId());
        if (value.sandboxId() != null) uuid(value.sandboxId());
        if (!("never_launched".equals(value.kind()) || "terminated_pod".equals(value.kind()) || "never_started_pod".equals(value.kind()) || "create_rejected".equals(value.kind()))
                || value.podName() == null || !value.podName().matches("oa-exec-[a-f0-9]{32}") || !hash(value.evidenceSha256())
                || !value.podAbsent() || !value.sandboxAbsent() || !value.creationSettled()) throw new RunFailure("invalid_cleanup_proof");
    }

    private static void validate(NotStarted value) {
        if (value == null) throw new RunFailure("invalid_cluster_not_started");
        uuid(value.id()); uuid(value.podUid()); version(value.resourceVersion());
        if (value.deletionTimestamp() == null || (value.schedulingGate() != null && !"open-agent.io/execution-authorized".equals(value.schedulingGate()))
                || !value.nodeUnbound() || !value.containerStatusesEmpty() || !hash(value.evidenceSha256()))
            throw new RunFailure("invalid_cluster_not_started");
    }

    private static void validate(CreationRejection value) {
        if (value == null) throw new RunFailure("invalid_cluster_creation_rejection");
        uuid(value.id()); uuid(value.auditId()); uuid(value.namespaceUid());
        if (value.httpStatus() == null || !Objects.equals(Map.of(401,"Unauthorized",403,"Forbidden",422,"Invalid").get(value.httpStatus()), value.reason())
                || value.reason() == null || value.podName() == null || !value.podName().matches("oa-exec-[a-f0-9]{32}")
                || !hash(value.requestSha256()) || !hash(value.responseSha256()))
            throw new RunFailure("invalid_cluster_creation_rejection");
    }
}
