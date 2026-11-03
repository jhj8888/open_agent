package com.iflytek.astron.console.commons.tenant;

/** Constructed only after querying membership; never deserialize this record from request bodies. */
public record TenantContext(String tenantId, TenantEnvironment environment, String uid,
        TenantRole role, String status, long tenantRevision, long membershipRevision) {

    public void require(TenantPermission permission) {
        if (!TenantAccessPolicy.allows(role, environment, status, permission)) {
            throw new TenantAccessDeniedException();
        }
    }
}
