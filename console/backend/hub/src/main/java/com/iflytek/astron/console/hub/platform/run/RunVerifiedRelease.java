package com.iflytek.astron.console.hub.platform.run;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.transaction.support.TransactionSynchronizationManager;

/** Shared effects AFTER the caller verifies runtime-specific evidence inside the attempt transaction. */
final class RunVerifiedRelease {
    private RunVerifiedRelease() {}

    static int commit(RunLedger ledger, RunSandboxJournal journal, JdbcTemplate jdbc, RunLedger.Lease lease,
            boolean hasSandbox, String supervisor, String proofId) {
        if (!TransactionSynchronizationManager.isActualTransactionActive()) throw new IllegalStateException("Release transaction required");
        if (hasSandbox) journal.releaseVerified(lease, supervisor, proofId);
        else ledger.resourcesReleased(lease);
        int verified = jdbc.update("""
                INSERT INTO oa_skill_verification(tenant_id,release_id,run_id,manifest_sha256,passed)
                SELECT r.tenant_id,d.release_id,r.run_id,d.manifest_sha256,true
                FROM oa_agent_run r JOIN oa_run_attempt a
                  ON a.tenant_id=r.tenant_id AND a.environment=r.environment AND a.run_id=r.run_id AND a.attempt_no=r.attempt_no
                JOIN oa_run_sandbox b ON b.lease_id=a.lease_id
                JOIN oa_agent_deployment_skill d
                  ON d.tenant_id=r.tenant_id AND d.environment=r.environment AND d.deployment_id=r.deployment_id
                JOIN oa_skill_release s ON s.tenant_id=d.tenant_id AND s.release_id=d.release_id
                WHERE a.lease_id=? AND r.environment='test' AND r.state='succeeded'
                  AND a.lease_state='released' AND b.phase='released' AND b.sandbox_id IS NOT NULL
                  AND s.status='ready' AND s.manifest_sha256=d.manifest_sha256
                  AND JSON_CONTAINS(r.snapshot_json,JSON_OBJECT('releaseId',d.release_id,'manifestSha256',d.manifest_sha256),'$.skills')
                ON DUPLICATE KEY UPDATE release_id=VALUES(release_id)
                """, lease.leaseId());
        jdbc.update("""
                INSERT INTO oa_tenant_audit(tenant_id,environment,actor_uid,action,subject_id,retain_until)
                SELECT tenant_id,environment,?,'execution.release.verified',?,TIMESTAMPADD(DAY,audit_retention_days,CURRENT_TIMESTAMP(3))
                FROM oa_tenant_environment WHERE tenant_id=? AND environment=?
                """, supervisor, lease.leaseId(), lease.scope().tenant(), lease.scope().environment());
        return verified;
    }
}
