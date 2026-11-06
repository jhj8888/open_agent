package com.iflytek.astron.console.hub.platform;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.iflytek.astron.console.commons.tenant.TenantAccessDeniedException;
import com.iflytek.astron.console.commons.tenant.TenantAccessService;
import com.iflytek.astron.console.commons.tenant.TenantEnvironment;
import com.iflytek.astron.console.commons.tenant.TenantPermission;
import com.iflytek.astron.console.commons.tenant.TenantRole;
import com.iflytek.astron.console.commons.util.RequestContextUtil;
import java.sql.PreparedStatement;
import java.sql.Statement;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import org.springframework.dao.OptimisticLockingFailureException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.support.GeneratedKeyHolder;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

/** Tenant governance uses the same transaction for authorization, mutation and audit. */
@Service
public class TenantGovernanceService {
    private final JdbcTemplate jdbc;
    private final TenantAccessService access;
    private final ObjectMapper json;

    public TenantGovernanceService(JdbcTemplate jdbc, TenantAccessService access, ObjectMapper json) {
        this.jdbc = jdbc;
        this.access = access;
        this.json = json;
    }

    public boolean isPlatformAdmin() {
        return Boolean.TRUE.equals(jdbc.queryForObject("""
                SELECT EXISTS(SELECT 1 FROM oa_platform_admin a JOIN user_info u
                  ON u.uid COLLATE utf8mb4_bin = a.uid
                  WHERE a.uid=? AND u.deleted=0 AND u.account_status=1)
                """, Boolean.class, RequestContextUtil.getUID()));
    }

    public List<Map<String, Object>> tenants() {
        String uid = RequestContextUtil.getUID();
        boolean admin = isPlatformAdmin();
        return jdbc.queryForList("""
                SELECT t.tenant_id AS tenantId, t.display_name AS displayName, t.scope_kind AS scopeKind,
                       t.status, t.revision, t.space_id AS spaceId
                FROM oa_tenant t
                WHERE ? OR EXISTS(SELECT 1 FROM oa_tenant_member m JOIN user_info u
                  ON u.uid COLLATE utf8mb4_bin = m.uid
                  WHERE m.tenant_id=t.tenant_id AND m.uid=? AND u.deleted=0 AND u.account_status=1
                    AND ((t.scope_kind='personal' AND t.personal_uid=m.uid)
                      OR EXISTS(SELECT 1 FROM agent_space s JOIN agent_space_user su ON su.space_id=s.id
                        WHERE s.id=t.space_id AND s.deleted=0 AND su.uid COLLATE utf8mb4_bin=m.uid)))
                ORDER BY t.created_at, t.tenant_id
                """, admin, uid);
    }

    public List<Map<String, Object>> memberships(String tenantId) {
        // Return only the caller's grants, never infer production access from a test grant.
        String uid = RequestContextUtil.getUID();
        return jdbc.queryForList("""
                SELECT m.environment, m.role, m.revision FROM oa_tenant_member m
                JOIN oa_tenant t ON t.tenant_id=m.tenant_id
                JOIN user_info u ON u.uid COLLATE utf8mb4_bin=m.uid
                WHERE m.tenant_id=? AND m.uid=? AND u.account_status=1 AND u.deleted=0
                  AND ((t.scope_kind='personal' AND t.personal_uid=m.uid)
                    OR EXISTS(SELECT 1 FROM agent_space s JOIN agent_space_user su ON su.space_id=s.id
                       WHERE s.id=t.space_id AND s.deleted=0 AND su.uid COLLATE utf8mb4_bin=m.uid))
                ORDER BY m.environment
                """, tenantId, uid);
    }

    @Transactional(readOnly=true, isolation=org.springframework.transaction.annotation.Isolation.REPEATABLE_READ)
    public Map<String, Object> quotas(String tenantId, String environment) {
        access.require(tenantId, environment, TenantPermission.METADATA_READ);
        var result=quotaRow(tenantId, environment);
        result.put("usage",jdbc.queryForMap("""
                SELECT (SELECT COUNT(*) FROM oa_agent_run WHERE tenant_id=? AND environment=? AND state='queued') AS queuedRuns,
                  (SELECT COUNT(*) FROM oa_run_attempt WHERE tenant_id=? AND environment=? AND lease_state<>'released') AS executionSlots,
                  (SELECT COUNT(*) FROM oa_run_sandbox WHERE tenant_id=? AND environment=? AND phase<>'released') AS sandboxes
                """,tenantId,environment,tenantId,environment,tenantId,environment));
        result.put("tokenBudgetEnforced",false);
        result.put("sampledAt",java.time.Instant.now());
        return result;
    }

    private Map<String, Object> quotaRow(String tenantId, String environment) {
        return jdbc.queryForMap("""
                SELECT max_concurrent_runs AS maxConcurrentRuns, max_queued_runs AS maxQueuedRuns,
                  max_sandboxes AS maxSandboxes, daily_token_budget AS dailyTokenBudget,
                  audit_retention_days AS auditRetentionDays, revision
                FROM oa_tenant_environment WHERE tenant_id=? AND environment=?
                """, tenantId, environment);
    }

    public List<Map<String, Object>> members(String tenantId, String environment) {
        access.require(tenantId, environment, TenantPermission.MEMBER_MANAGE);
        return jdbc.queryForList("""
                SELECT m.uid,m.role,m.revision,u.username FROM oa_tenant_member m
                JOIN user_info u ON u.uid COLLATE utf8mb4_bin=m.uid
                WHERE m.tenant_id=? AND m.environment=? ORDER BY m.uid
                """, tenantId, environment);
    }

    @Transactional
    public String createTenant(String displayName, String ownerUid) {
        requirePlatformAdmin();
        requireActiveUser(ownerUid);
        GeneratedKeyHolder key = new GeneratedKeyHolder();
        jdbc.update(connection -> {
            PreparedStatement statement = connection.prepareStatement(
                    "INSERT INTO agent_space(name,uid,type,deleted) VALUES (?,?,4,0)", Statement.RETURN_GENERATED_KEYS);
            statement.setString(1, displayName);
            statement.setString(2, ownerUid);
            return statement;
        }, key);
        long spaceId = Objects.requireNonNull(key.getKey()).longValue();
        String tenantId = "space-" + spaceId;
        jdbc.update("INSERT INTO agent_space_user(space_id,uid,role) VALUES (?,?,1)", spaceId, ownerUid);
        jdbc.update("INSERT INTO oa_tenant(tenant_id,display_name,scope_kind,space_id) VALUES (?,?,'space',?)",
                tenantId, displayName, spaceId);
        jdbc.update("INSERT INTO oa_tenant_environment(tenant_id,environment) VALUES (?,'test'),(?,'prod')", tenantId, tenantId);
        jdbc.update("INSERT INTO oa_tenant_member(tenant_id,environment,uid,role) VALUES (?,'test',?,'tenant_admin')", tenantId, ownerUid);
        audit(tenantId, "test", "tenant.created", tenantId, null, Map.of("displayName", displayName, "ownerUid", ownerUid));
        return tenantId;
    }

    @Transactional
    public void setStatus(String tenantId, String status, long revision) {
        requirePlatformAdmin();
        if (!List.of("active", "suspended", "archived").contains(status)) {
            throw new IllegalArgumentException("Invalid tenant status");
        }
        Map<String, Object> old = lockTenant(tenantId);
        if ("archived".equals(old.get("status")) && !"archived".equals(status)) {
            throw new IllegalArgumentException("Archived tenants cannot be reactivated");
        }
        changed(jdbc.update("UPDATE oa_tenant SET status=?,revision=revision+1 WHERE tenant_id=? AND revision=?", status, tenantId, revision));
        for (TenantEnvironment environment : TenantEnvironment.values()) {
            audit(tenantId, environment.value(), "tenant.status_changed", tenantId,
                    Map.of("status", old.get("status"), "revision", old.get("revision")), Map.of("status", status, "revision", revision + 1));
        }
    }

    @Transactional
    public void setQuotas(String tenantId, String environment, PlatformTenantController.QuotaUpdate update) {
        lockTenant(tenantId);
        access.require(tenantId, environment, TenantPermission.QUOTA_MANAGE);
        Map<String, Object> old = quotaRow(tenantId, environment);
        changed(jdbc.update("""
                UPDATE oa_tenant_environment SET max_concurrent_runs=?,max_queued_runs=?,max_sandboxes=?,
                  daily_token_budget=?,audit_retention_days=?,revision=revision+1
                WHERE tenant_id=? AND environment=? AND revision=?
                """, update.maxConcurrentRuns(), update.maxQueuedRuns(), update.maxSandboxes(), update.dailyTokenBudget(),
                update.auditRetentionDays(), tenantId, environment, update.revision()));
        audit(tenantId, environment, "tenant.quota_changed", tenantId, old, quotaRow(tenantId, environment));
    }

    @Transactional
    public void setMember(String tenantId, String environment, String uid, String role, Long revision, boolean bootstrap) {
        TenantEnvironment.parse(environment);
        TenantRole.parse(role);
        Map<String, Object> tenant = lockTenant(tenantId);
        if (bootstrap) {
            requirePlatformAdmin();
            if (!TenantRole.TENANT_ADMIN.value().equals(role)) {
                throw new IllegalArgumentException("Bootstrap can only enroll an environment administrator");
            }
        } else {
            access.require(tenantId, environment, TenantPermission.MEMBER_MANAGE);
        }
        if (!"active".equals(tenant.get("status"))) {
            throw new TenantAccessDeniedException();
        }
        requireActiveUser(uid);
        if ("personal".equals(tenant.get("scope_kind")) && !uid.equals(tenant.get("personal_uid"))) {
            throw new TenantAccessDeniedException();
        }
        List<Map<String, Object>> existing = jdbc.queryForList(
                "SELECT role,revision FROM oa_tenant_member WHERE tenant_id=? AND environment=? AND uid=?", tenantId, environment, uid);
        Map<String, Object> old = existing.isEmpty() ? null : existing.getFirst();
        if (old != null && TenantRole.TENANT_ADMIN.value().equals(old.get("role")) && !TenantRole.TENANT_ADMIN.value().equals(role)) {
            requireAnotherAdmin(tenantId, environment, uid);
        }
        if (tenant.get("space_id") != null) {
            // Environment roles remain authoritative; legacy membership is only an enrollment boundary.
            jdbc.update("INSERT INTO agent_space_user(space_id,uid,role) VALUES (?,?,3) ON DUPLICATE KEY UPDATE uid=uid", tenant.get("space_id"), uid);
        }
        if (old == null) {
            if (revision != null) {
                throw new OptimisticLockingFailureException("Membership changed; refresh before retrying");
            }
            jdbc.update("INSERT INTO oa_tenant_member(tenant_id,environment,uid,role) VALUES (?,?,?,?)", tenantId, environment, uid, role);
        } else {
            if (revision == null) {
                throw new OptimisticLockingFailureException("Membership already exists; refresh before retrying");
            }
            changed(jdbc.update("UPDATE oa_tenant_member SET role=?,source='explicit',revision=revision+1 WHERE tenant_id=? AND environment=? AND uid=? AND revision=?",
                    role, tenantId, environment, uid, revision));
        }
        audit(tenantId, environment, bootstrap ? "tenant.admin_enrolled" : "tenant.member_changed", uid, old,
                Map.of("role", role, "revision", revision == null ? 0 : revision + 1));
    }

    @Transactional
    public void removeMember(String tenantId, String environment, String uid, long revision) {
        lockTenant(tenantId);
        access.require(tenantId, environment, TenantPermission.MEMBER_MANAGE);
        List<Map<String, Object>> rows = jdbc.queryForList(
                "SELECT role,revision FROM oa_tenant_member WHERE tenant_id=? AND environment=? AND uid=?", tenantId, environment, uid);
        if (rows.isEmpty()) {
            throw new TenantAccessDeniedException();
        }
        if (TenantRole.TENANT_ADMIN.value().equals(rows.getFirst().get("role"))) {
            requireAnotherAdmin(tenantId, environment, uid);
        }
        changed(jdbc.update("DELETE FROM oa_tenant_member WHERE tenant_id=? AND environment=? AND uid=? AND revision=?", tenantId, environment, uid, revision));
        audit(tenantId, environment, "tenant.member_removed", uid, rows.getFirst(), null);
    }

    public List<Map<String, Object>> auditEntries(String tenantId, String environment, long beforeId) {
        return auditEntries(tenantId,environment,beforeId,"");
    }

    public List<Map<String, Object>> auditEntries(String tenantId, String environment, long beforeId, String action) {
        access.require(tenantId, environment, TenantPermission.AUDIT_READ);
        if(beforeId<1 || action==null || (!action.isEmpty()&&!action.matches("[a-zA-Z0-9_.-]{1,64}")))
            throw new IllegalArgumentException("Invalid audit query");
        var rows=jdbc.queryForList("""
                SELECT id,CAST(id AS CHAR) AS `cursor`,actor_uid AS actorUid,action,subject_id AS subjectId,before_value AS beforeValue,
                  after_value AS afterValue,created_at AS createdAt,retain_until AS retainUntil
                FROM oa_tenant_audit WHERE tenant_id=? AND environment=? AND id<? AND (?='' OR action=?) ORDER BY id DESC LIMIT 101
                """, tenantId, environment, beforeId,action,action);
        boolean more=rows.size()>100;
        var page=new java.util.ArrayList<>(rows.subList(0,Math.min(rows.size(),100)));
        if(!page.isEmpty()) page.getLast().put("nextBeforeId",more?page.getLast().get("cursor"):null);
        return page;
    }

    private void requireAnotherAdmin(String tenantId, String environment, String uid) {
        Integer count = jdbc.queryForObject("""
                SELECT COUNT(*) FROM oa_tenant_member m JOIN user_info u ON u.uid COLLATE utf8mb4_bin=m.uid
                WHERE m.tenant_id=? AND m.environment=? AND m.role='tenant_admin' AND m.uid<>?
                  AND u.deleted=0 AND u.account_status=1
                  AND EXISTS(SELECT 1 FROM oa_tenant t WHERE t.tenant_id=m.tenant_id
                    AND ((t.scope_kind='personal' AND t.personal_uid=m.uid)
                      OR (t.scope_kind='space' AND EXISTS(SELECT 1 FROM agent_space s
                        JOIN agent_space_user su ON su.space_id=s.id
                        WHERE s.id=t.space_id AND s.deleted=0 AND su.uid COLLATE utf8mb4_bin=m.uid))))
                """, Integer.class, tenantId, environment, uid);
        if (count == null || count == 0) {
            throw new IllegalArgumentException("Keep at least one active environment administrator");
        }
    }

    private void requirePlatformAdmin() {
        if (!isPlatformAdmin()) {
            throw new TenantAccessDeniedException();
        }
    }

    private void requireActiveUser(String uid) {
        Integer count = jdbc.queryForObject("SELECT COUNT(*) FROM user_info WHERE uid COLLATE utf8mb4_bin=? AND deleted=0 AND account_status=1", Integer.class, uid);
        if (count == null || count != 1) {
            throw new TenantAccessDeniedException();
        }
    }

    private Map<String, Object> lockTenant(String tenantId) {
        List<Map<String, Object>> rows = jdbc.queryForList("SELECT * FROM oa_tenant WHERE tenant_id=? FOR UPDATE", tenantId);
        if (rows.isEmpty()) {
            throw new TenantAccessDeniedException();
        }
        return rows.getFirst();
    }

    private void changed(int rows) {
        if (rows != 1) {
            throw new OptimisticLockingFailureException("Configuration changed; refresh before retrying");
        }
    }

    private void audit(String tenantId, String environment, String action, String subject, Object before, Object after) {
        try {
            jdbc.update("""
                    INSERT INTO oa_tenant_audit(tenant_id,environment,actor_uid,action,subject_id,before_value,after_value,retain_until)
                    SELECT tenant_id,environment,?,?,?,?,?,TIMESTAMPADD(DAY,audit_retention_days,CURRENT_TIMESTAMP(3))
                    FROM oa_tenant_environment WHERE tenant_id=? AND environment=?
                    """, RequestContextUtil.getUID(), action, subject, before == null ? null : json.writeValueAsString(before),
                    after == null ? null : json.writeValueAsString(after), tenantId, environment);
        } catch (JsonProcessingException exception) {
            throw new IllegalStateException("Cannot serialize tenant audit", exception);
        }
    }
}
