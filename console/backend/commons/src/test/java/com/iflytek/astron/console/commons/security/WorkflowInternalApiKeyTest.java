package com.iflytek.astron.console.commons.security;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.NullSource;
import org.junit.jupiter.params.provider.ValueSource;

class WorkflowInternalApiKeyTest {

    @Test
    void requireConfiguredAcceptsAndTrimsStrongCredential() {
        String credential = "0123456789abcdef0123456789abcdef";

        assertThat(WorkflowInternalApiKey.requireConfigured("  " + credential + "  "))
                .isEqualTo(credential);
    }

    @ParameterizedTest
    @NullSource
    @ValueSource(strings = {
            "",
            "short-internal-key",
            "CHANGE_ME_WORKFLOW_INTERNAL_API_KEY",
            "0123456789abcdef\r0123456789abcdef",
            "0123456789abcdef\n0123456789abcdef"
    })
    void requireConfiguredRejectsMissingDefaultOrHeaderInjectionValues(String credential) {
        assertThatThrownBy(() -> WorkflowInternalApiKey.requireConfigured(credential))
                .isInstanceOf(IllegalStateException.class)
                .hasMessage(
                        "WORKFLOW_INTERNAL_API_KEY must contain a non-default value of at least 32 characters");
    }
}
