package com.iflytek.astron.console.commons.tenant;

import java.util.List;
import java.util.Optional;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;

/** Database membership remains authoritative: no headers or stale role caches grant access. */
@Repository
public class TenantAccessRepository {
    private final JdbcTemplate jdbc;

    public TenantAccessRepository(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    public Optional<TenantContext> findContext(String uid, String tenantId, TenantEnvironment environment) {
        List<TenantContext> contexts = jdbc.query("""
                SELECT t.tenant_id, t.status, t.revision AS tenant_revision,
                       m.role, m.revision AS membership_revision
                FROM oa_tenant t
                JOIN oa_tenant_member m ON m.tenant_id = t.tenant_id
                JOIN user_info u ON u.uid COLLATE utf8mb4_bin = m.uid
                WHERE t.tenant_id = ? AND m.environment = ? AND m.uid = ?
                  AND u.deleted = 0 AND u.account_status = 1
                  AND ((t.scope_kind = 'personal' AND t.personal_uid = m.uid)
                    OR (t.scope_kind = 'space' AND EXISTS (
                        SELECT 1 FROM agent_space s JOIN agent_space_user su ON su.space_id = s.id
                        WHERE s.id = t.space_id AND s.deleted = 0 AND su.uid COLLATE utf8mb4_bin = m.uid)))
                """, (rs, row) -> new TenantContext(rs.getString("tenant_id"), environment, uid,
                TenantRole.parse(rs.getString("role")), rs.getString("status"),
                rs.getLong("tenant_revision"), rs.getLong("membership_revision")), tenantId, environment.value(), uid);
        return contexts.stream().findFirst();
    }
}
