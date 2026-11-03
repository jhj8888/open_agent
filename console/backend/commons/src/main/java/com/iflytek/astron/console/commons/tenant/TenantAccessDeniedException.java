package com.iflytek.astron.console.commons.tenant;

import org.springframework.security.access.AccessDeniedException;

/** Identical response for an absent scope and an inaccessible scope. */
public final class TenantAccessDeniedException extends AccessDeniedException {
    private static final long serialVersionUID = 1L;

    public TenantAccessDeniedException() {
        super("Tenant resource is unavailable");
    }
}
