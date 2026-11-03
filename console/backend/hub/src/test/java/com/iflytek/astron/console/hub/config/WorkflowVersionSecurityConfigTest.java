package com.iflytek.astron.console.hub.config;

import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.iflytek.astron.console.commons.config.JwtClaimsFilter;
import com.iflytek.astron.console.commons.data.UserInfoDataService;
import com.iflytek.astron.console.hub.config.security.RestfulAccessDeniedHandler;
import com.iflytek.astron.console.hub.config.security.RestfulAuthenticationEntryPoint;
import com.iflytek.astron.console.toolkit.controller.workflow.VersionController;
import com.iflytek.astron.console.toolkit.entity.table.workflow.WorkflowVersion;
import com.iflytek.astron.console.toolkit.security.ArtifactUploadTokenProvider;
import com.iflytek.astron.console.toolkit.security.SandboxRuntimeCredentialTokenProvider;
import com.iflytek.astron.console.toolkit.service.workflow.VersionService;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.boot.test.mock.mockito.MockBean;
import org.springframework.context.annotation.Import;
import org.springframework.http.MediaType;
import org.springframework.security.oauth2.jwt.JwtDecoder;
import org.springframework.test.web.servlet.MockMvc;

@WebMvcTest(
        controllers = VersionController.class,
        properties = {
                "skill.sandbox.artifact-upload-token=0123456789abcdef0123456789abcdef",
                "skill.sandbox.runtime-credential.token=abcdef0123456789abcdef0123456789"
        })
@Import({
        SecurityConfig.class,
        JwtClaimsFilter.class,
        RestfulAuthenticationEntryPoint.class,
        RestfulAccessDeniedHandler.class
})
class WorkflowVersionSecurityConfigTest {

    @Autowired
    private MockMvc mockMvc;

    @MockBean
    private VersionService versionService;

    @MockBean
    private UserInfoDataService userInfoDataService;

    @MockBean
    private JwtDecoder jwtDecoder;

    @MockBean
    private ArtifactUploadTokenProvider artifactUploadTokenProvider;

    @MockBean
    private SandboxRuntimeCredentialTokenProvider sandboxRuntimeCredentialTokenProvider;

    @Test
    void anonymousLegacyUpdateChannelAliasIsRejectedBeforeControllerInvocation()
            throws Exception {
        mockMvc.perform(post("/workflow/version/update_channel_result")
                .contentType(MediaType.APPLICATION_JSON)
                .content("{\"id\":99,\"publishResult\":\"Success\"}"))
                .andExpect(status().is4xxClientError());

        verify(versionService, never()).update_channel_result(
                org.mockito.ArgumentMatchers.any(WorkflowVersion.class));
    }
}
