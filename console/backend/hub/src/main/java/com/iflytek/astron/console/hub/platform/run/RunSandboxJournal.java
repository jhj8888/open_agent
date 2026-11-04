package com.iflytek.astron.console.hub.platform.run;

import java.util.Map;
import java.util.Set;
import java.util.UUID;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;

/**
 * Durable one-sandbox-per-attempt admission. All mutations take the ledger's tenant/environment/
 * run/attempt locks first; no sandbox or other network I/O is performed in these transactions.
 * Worker acknowledgements describe observations, never independent proof of resource release.
 */
@Component
public class RunSandboxJournal {
    private final RunLedger ledger;
    private final JdbcTemplate jdbc;

    public RunSandboxJournal(RunLedger ledger, JdbcTemplate jdbc) { this.ledger = ledger; this.jdbc = jdbc; }

    public void reserve(RunLedger.Lease lease, String profile, String image) {
        if (!Set.of("kata", "gvisor-demo").contains(profile == null ? "" : profile)
                || (lease.scope().environment().equals("prod") && !"kata".equals(profile))
                || image == null || image.length() > 512
                || !image.matches("[A-Za-z0-9][A-Za-z0-9._:/-]*@sha256:[a-f0-9]{64}"))
            throw new RunFailure("invalid_sandbox_configuration");
        ledger.withPermit(lease, () -> {
            // Bind the runtime to the immutable deployment's releases, not an arbitrary worker image.
            var images = jdbc.queryForList("""
                    SELECT DISTINCT s.runtime_image FROM oa_agent_run r
                    JOIN oa_agent_deployment_skill b ON b.tenant_id=r.tenant_id AND b.environment=r.environment
                        AND b.deployment_id=r.deployment_id
                    JOIN oa_skill_release s ON s.tenant_id=b.tenant_id AND s.release_id=b.release_id
                    WHERE r.tenant_id=? AND r.environment=? AND r.run_id=?
                    """, String.class, lease.scope().tenant(), lease.scope().environment(), lease.runId());
            if (images.size() != 1 || !image.equals(images.getFirst())) throw new RunFailure("invalid_sandbox_configuration");
            var rows = jdbc.queryForList("SELECT * FROM oa_run_sandbox WHERE lease_id=?", lease.leaseId());
            if (!rows.isEmpty()) {
                var row = rows.getFirst();
                if (!profile.equals(row.get("isolation_profile")) || !image.equals(row.get("runtime_image"))
                        || !"reserved".equals(row.get("phase"))) throw new RunFailure("sandbox_state_conflict");
                return null;
            }
            long used = jdbc.queryForObject("SELECT COUNT(*) FROM oa_run_sandbox WHERE tenant_id=? AND environment=? AND phase<>'released'",
                    Long.class, lease.scope().tenant(), lease.scope().environment());
            long maximum = jdbc.queryForObject("SELECT max_sandboxes FROM oa_tenant_environment WHERE tenant_id=? AND environment=?",
                    Long.class, lease.scope().tenant(), lease.scope().environment());
            if (used >= maximum) throw new RunFailure("sandbox_quota_full");
            jdbc.update("INSERT INTO oa_run_sandbox(tenant_id,environment,run_id,attempt_no,lease_id,isolation_profile,runtime_image) VALUES(?,?,?,?,?,?,?)",
                    lease.scope().tenant(), lease.scope().environment(), lease.runId(), lease.attempt(), lease.leaseId(), profile, image);
            return null;
        });
    }

    /** One-shot durable authorization immediately before SDK create. A lost response is NOT retryable. */
    public void creating(RunLedger.Lease lease) {
        ledger.withPermit(lease, () -> {
            if (jdbc.update("UPDATE oa_run_sandbox SET phase='creating',creation_started_at=CURRENT_TIMESTAMP(3) WHERE lease_id=? AND phase='reserved'",
                    lease.leaseId()) != 1) throw new RunFailure("sandbox_state_conflict");
            return null;
        });
    }

    public void created(RunLedger.Lease lease, String sandboxId) { report(lease, sandboxId, "created"); }
    public void cleanupPending(RunLedger.Lease lease, String sandboxId) { report(lease, sandboxId, "cleanup_pending"); }
    public void destructionReported(RunLedger.Lease lease, String sandboxId) { report(lease, sandboxId, "destruction_reported"); }

    private void report(RunLedger.Lease lease, String sandboxId, String observation) {
        uuid(sandboxId);
        ledger.withAttempt(lease, () -> {
            var row = row(lease);
            Object previous = row.get("sandbox_id");
            String phase = (String) row.get("phase");
            if (previous != null && !sandboxId.equals(previous)) throw new RunFailure("sandbox_state_conflict");
            // A late callback cannot resurrect a released reservation or bind a previously unknown ID.
            if (phase.equals("released")) {
                if (previous == null) throw new RunFailure("sandbox_state_conflict");
                return null;
            }
            if (phase.equals("reserved")) throw new RunFailure("sandbox_state_conflict");
            String next = observation.equals("created") && !phase.equals("cleanup_pending") ? "created" : "cleanup_pending";
            jdbc.update("UPDATE oa_run_sandbox SET sandbox_id=?,created_at=COALESCE(created_at,CURRENT_TIMESTAMP(3)),phase=?,"
                    + "destruction_reported_at=IF(?,COALESCE(destruction_reported_at,CURRENT_TIMESTAMP(3)),destruction_reported_at) WHERE lease_id=?",
                    sandboxId, next, observation.equals("destruction_reported"), lease.leaseId());
            return null;
        });
    }

    /**
     * IN-PROCESS TRUSTED RECONCILER ONLY; deliberately absent from worker HTTP controllers.
     * Caller must independently prove the execution process stopped AND no sandbox or in-flight
     * creation can survive. Expired leases, worker destroy reports and empty list responses are
     * insufficient. verificationId refers to that verifier's retained evidence.
     */
    public void releaseVerified(RunLedger.Lease lease, String verifier, String verificationId) {
        uuid(verificationId);
        if (verifier == null || !verifier.matches("[A-Za-z0-9_.:-]{1,128}")) throw new RunFailure("invalid_cleanup_verifier");
        ledger.withAttempt(lease, () -> {
            var row = row(lease);
            if (!RunState.parse((String) ledger.get(lease.scope(), lease.runId()).get("state")).terminal())
                throw new RunFailure("cleanup_before_terminal");
            if (!"released".equals(row.get("phase"))) {
                jdbc.update("UPDATE oa_run_sandbox SET phase='released',released_at=CURRENT_TIMESTAMP(3),verified_by=?,verification_id=? WHERE lease_id=?",
                        verifier, verificationId, lease.leaseId());
                jdbc.update("""
                        INSERT INTO oa_tenant_audit(tenant_id,environment,actor_uid,action,subject_id,retain_until)
                        SELECT tenant_id,environment,?,'sandbox.release.verified',?,
                            TIMESTAMPADD(DAY,audit_retention_days,CURRENT_TIMESTAMP(3))
                        FROM oa_tenant_environment WHERE tenant_id=? AND environment=?
                        """, verifier, lease.leaseId(), lease.scope().tenant(), lease.scope().environment());
            }
            // Same transaction: neither quota is released if the other update or audit fails.
            ledger.resourcesReleased(lease);
            return null;
        });
    }

    private Map<String, Object> row(RunLedger.Lease lease) {
        var rows = jdbc.queryForList("SELECT * FROM oa_run_sandbox WHERE lease_id=? FOR UPDATE", lease.leaseId());
        if (rows.isEmpty()) throw new RunFailure("sandbox_state_conflict");
        return rows.getFirst();
    }
    private static void uuid(String value) {
        try { if (!UUID.fromString(value).toString().equals(value)) throw new IllegalArgumentException(); }
        catch (RuntimeException exception) { throw new RunFailure("invalid_identifier"); }
    }
}
