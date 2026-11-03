package com.iflytek.astron.console.commons.security;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;

class WorkflowGatewayIdentityTest {

    @Test
    void signatureMatchesCrossLanguageFixedVector() {
        assertThat(WorkflowGatewayIdentity.sign(
                "g".repeat(32),
                "POST",
                "/workflow/v1/chat/completions",
                "gateway-app",
                1_700_000_000L))
                .isEqualTo(
                        "6261d5595286ac9407951e6c4c690bd1c50c2fbfe4f29addc50e3f437ef6a871");
    }

    @Test
    void authorizedPathExcludesQueryWithoutDecodingThePath() {
        assertThat(WorkflowGatewayIdentity.requireAuthorizedPath(
                "POST", "/workflow/v1/resume?trace_id=123"))
                .isEqualTo("/workflow/v1/resume");
    }

    @ParameterizedTest
    @CsvSource({
            "GET,/workflow/v1/chat/completions",
            "POST,/workflow/v1/run",
            "POST,/workflow/v1/chat%2Fcompletions",
            "POST,/workflow/v1/chat/completions#fragment"
    })
    void unsupportedMethodOrAlternatePathFailsClosed(String method, String uri) {
        assertThatThrownBy(() -> WorkflowGatewayIdentity.requireAuthorizedPath(method, uri))
                .isInstanceOf(IllegalArgumentException.class);
    }
}
