package com.iflytek.astron.console.commons.security;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.NullSource;
import org.junit.jupiter.params.provider.ValueSource;

class TenantInternalApiKeyTest {

    @Test
    void requireConfiguredAcceptsAndTrimsStrongCredential() {
        String credential = "0123456789abcdef._~-0123456789abcdef";

        assertThat(TenantInternalApiKey.requireConfigured("  " + credential + "  "))
                .isEqualTo(credential);
    }

    @ParameterizedTest
    @NullSource
    @ValueSource(strings = {
            "",
            "short-tenant-secret",
            "0123456789abcdef\r0123456789abcdef",
            "0123456789abcdef\n0123456789abcdef",
            "0123456789abcdef!0123456789abcdef",
            "0123456789abcdef中文0123456789abcdef",
            "012345678901234567890123456789012345678901234567890",
            "NjhmY2NmM2NkZDE4MDFlNmM5ZjcyZjMy"
    })
    void requireConfiguredRejectsValuesOutsideTenantBootstrapContract(String credential) {
        assertThatThrownBy(() -> TenantInternalApiKey.requireConfigured(credential))
                .isInstanceOf(IllegalStateException.class)
                .hasMessage(
                        "TENANT_SECRET must contain 32-50 safe characters and must not use the published legacy value");
    }
}
