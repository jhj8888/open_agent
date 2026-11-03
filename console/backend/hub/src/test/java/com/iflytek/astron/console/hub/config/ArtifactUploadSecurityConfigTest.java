package com.iflytek.astron.console.hub.config;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.multipart;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.iflytek.astron.console.commons.config.JwtClaimsFilter;
import com.iflytek.astron.console.commons.data.UserInfoDataService;
import com.iflytek.astron.console.hub.config.security.ArtifactUploadTokenAuthenticationFilter;
import com.iflytek.astron.console.hub.config.security.RestfulAccessDeniedHandler;
import com.iflytek.astron.console.hub.config.security.RestfulAuthenticationEntryPoint;
import com.iflytek.astron.console.toolkit.controller.workflow.WorkflowArtifactController;
import com.iflytek.astron.console.toolkit.entity.dto.workflow.WorkflowArtifactDto;
import com.iflytek.astron.console.toolkit.security.ArtifactUploadTokenProvider;
import com.iflytek.astron.console.toolkit.security.SandboxRuntimeCredentialTokenProvider;
import com.iflytek.astron.console.toolkit.service.workflow.WorkflowArtifactService;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.boot.test.mock.mockito.MockBean;
import org.springframework.context.annotation.Import;
import org.springframework.security.oauth2.jwt.JwtDecoder;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.mock.web.MockMultipartFile;

@WebMvcTest(
        controllers = WorkflowArtifactController.class,
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
class ArtifactUploadSecurityConfigTest {

    private static final String VALID_TOKEN = "0123456789abcdef0123456789abcdef";

    @Autowired
    private MockMvc mockMvc;

    @MockBean
    private WorkflowArtifactService workflowArtifactService;

    @MockBean
    private UserInfoDataService userInfoDataService;

    @MockBean
    private JwtDecoder jwtDecoder;

    @MockBean
    private ArtifactUploadTokenProvider artifactUploadTokenProvider;

    @MockBean
    private SandboxRuntimeCredentialTokenProvider sandboxRuntimeCredentialTokenProvider;

    @Test
    void rejectsUploadWithoutServiceToken() throws Exception {
        mockMvc.perform(validUploadRequest()).andExpect(status().isUnauthorized());

        verify(workflowArtifactService, never()).uploadInternal(
                any(), any(), any(), any(), any(), any(), any(), any(), any(), any());
    }

    @Test
    void rejectsUploadWithWrongServiceToken() throws Exception {
        mockMvc.perform(validUploadRequest().header(
                ArtifactUploadTokenAuthenticationFilter.TOKEN_HEADER, "wrong-token"))
                .andExpect(status().isUnauthorized());

        verify(workflowArtifactService, never()).uploadInternal(
                any(), any(), any(), any(), any(), any(), any(), any(), any(), any());
    }

    @Test
    void allowsUploadWithValidServiceToken() throws Exception {
        when(artifactUploadTokenProvider.matches(VALID_TOKEN)).thenReturn(true);
        when(workflowArtifactService.uploadInternal(
                        eq(VALID_TOKEN),
                        eq(null),
                        eq("flow-1"),
                        eq("user-1"),
                        eq(null),
                        eq("run-1"),
                        eq("node-1"),
                        eq(null),
                        eq("skill_sandbox"),
                        any()))
                .thenReturn(new WorkflowArtifactDto());

        mockMvc.perform(validUploadRequest().header(
                        ArtifactUploadTokenAuthenticationFilter.TOKEN_HEADER, VALID_TOKEN))
                .andExpect(status().isOk());

        verify(workflowArtifactService).uploadInternal(
                eq(VALID_TOKEN),
                eq(null),
                eq("flow-1"),
                eq("user-1"),
                eq(null),
                eq("run-1"),
                eq("node-1"),
                eq(null),
                eq("skill_sandbox"),
                any());
    }

    private org.springframework.test.web.servlet.request.MockHttpServletRequestBuilder validUploadRequest() {
        MockMultipartFile file = new MockMultipartFile(
                "file", "output.txt", "text/plain", "hello".getBytes());
        return multipart("/workflow/artifacts/internal-upload")
                .file(file)
                .param("flowId", "flow-1")
                .param("uid", "user-1")
                .param("runId", "run-1")
                .param("nodeId", "node-1")
                .param("source", "skill_sandbox");
    }
}
