package com.iflytek.astron.console.commons.tenant;

public enum TenantRole {
    TENANT_ADMIN("tenant_admin"), DEVELOPER("developer"), OPERATOR("operator"), VIEWER("viewer");

    private final String value;

    TenantRole(String value) {
        this.value = value;
    }

    public String value() {
        return value;
    }

    public static TenantRole parse(String value) {
        for (TenantRole role : values()) {
            if (role.value.equals(value)) {
                return role;
            }
        }
        throw new IllegalArgumentException("unrecognized tenant role");
    }
}
