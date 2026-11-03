package com.iflytek.astron.console.hub.config;

import static org.assertj.core.api.Assertions.assertThat;

import org.junit.jupiter.api.Test;

class WebMvcConfigTest {

    @Test
    void noAuthApisExcludeSkillSandboxArtifactInternalUpload() {
        assertThat(WebMvcConfig.NO_AUTH_REQUIRED_APIS)
                .doesNotContain("/workflow/artifacts/internal-upload");
    }

    @Test
    void workflowMutationAndMetadataRoutesAlwaysRequireAuthentication() {
        assertThat(WebMvcConfig.NO_AUTH_REQUIRED_APIS)
                .doesNotContain(
                        "/workflow/version/update_channel_result",
                        "/workflow/copyFlow",
                        "/workflow/copy-flow",
                        "/workflow/hasQaNode",
                        "/workflow/has-qa-node");
    }
}
