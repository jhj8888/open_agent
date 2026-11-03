package com.iflytek.astron.console.commons.tenant;

import java.util.Set;

/** Central role matrix; a role never substitutes for a production deployment execution grant. */
public final class TenantAccessPolicy {
    private static final Set<TenantPermission> GOVERNANCE = Set.of(
            TenantPermission.MEMBER_MANAGE, TenantPermission.QUOTA_MANAGE,
            TenantPermission.CREDENTIAL_MANAGE, TenantPermission.AUDIT_READ);

    private TenantAccessPolicy() {}

    public static boolean allows(TenantRole role, TenantEnvironment environment, String status,
            TenantPermission permission) {
        if (role == null || environment == null || permission == null || status == null) {
            return false;
        }
        if (!Set.of("active", "suspended", "archived").contains(status)) {
            return false;
        }
        if (!"active".equals(status)) {
            // Only authorized administrators/operators retain history and explicit cancellation.
            return (role == TenantRole.TENANT_ADMIN || role == TenantRole.OPERATOR)
                    && (permission == TenantPermission.METADATA_READ || permission == TenantPermission.LOG_READ
                            || permission == TenantPermission.AUDIT_READ
                            || (permission == TenantPermission.RUN_CANCEL && "suspended".equals(status)));
        }
        if (permission == TenantPermission.METADATA_READ) {
            return true;
        }
        if (GOVERNANCE.contains(permission)) {
            return role == TenantRole.TENANT_ADMIN;
        }
        if (permission == TenantPermission.DEPLOYMENT_PUBLISH) {
            return environment == TenantEnvironment.PROD && role == TenantRole.OPERATOR;
        }
        if (permission == TenantPermission.LOG_READ || permission == TenantPermission.RUN_CANCEL) {
            return role == TenantRole.TENANT_ADMIN || role == TenantRole.OPERATOR
                    || (environment == TenantEnvironment.TEST && role == TenantRole.DEVELOPER);
        }
        if (environment == TenantEnvironment.TEST) {
            if (permission == TenantPermission.RESOURCE_WRITE) {
                return role == TenantRole.TENANT_ADMIN || role == TenantRole.DEVELOPER;
            }
            if (permission == TenantPermission.RUN_CREATE) {
                return role != TenantRole.VIEWER;
            }
        }
        return false;
    }
}
