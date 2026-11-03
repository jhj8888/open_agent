package com.iflytek.astron.console.commons.tenant;

/** Environment is explicit. Unknown or omitted values must never become production access. */
public enum TenantEnvironment {
    TEST("test"), PROD("prod");

    private final String value;

    TenantEnvironment(String value) {
        this.value = value;
    }

    public String value() {
        return value;
    }

    public static TenantEnvironment parse(String value) {
        for (TenantEnvironment environment : values()) {
            if (environment.value.equals(value)) {
                return environment;
            }
        }
        throw new IllegalArgumentException("environment must be test or prod");
    }
}
