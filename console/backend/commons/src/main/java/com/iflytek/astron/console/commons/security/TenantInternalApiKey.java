package com.iflytek.astron.console.commons.security;

import org.apache.commons.lang3.StringUtils;

import java.util.regex.Pattern;

/** Shared validation and header naming for trusted calls to the core tenant service. */
public final class TenantInternalApiKey {

    public static final String HEADER = "X-Tenant-Internal-Key";

    private static final int MIN_LENGTH = 32;
    private static final int MAX_LENGTH = 50;
    private static final String LEGACY_PUBLIC_SECRET = "NjhmY2NmM2NkZDE4MDFlNmM5ZjcyZjMy";
    private static final Pattern SAFE_VALUE = Pattern.compile("[A-Za-z0-9._~-]+");

    private TenantInternalApiKey() {}

    /** Return a normalized credential or fail closed before issuing an internal request. */
    public static String requireConfigured(String configuredValue) {
        String apiKey = StringUtils.trimToEmpty(configuredValue);
        if (apiKey.length() < MIN_LENGTH
                || apiKey.length() > MAX_LENGTH
                || !SAFE_VALUE.matcher(apiKey).matches()
                || LEGACY_PUBLIC_SECRET.equals(apiKey)) {
            throw new IllegalStateException(
                    "TENANT_SECRET must contain 32-50 safe characters and must not use the published legacy value");
        }
        return apiKey;
    }
}
