package com.iflytek.astron.console.hub.config;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.ArgumentMatchers.isNull;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.header;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import java.time.Instant;

import com.iflytek.astron.console.commons.config.JwtClaimsFilter;
import com.iflytek.astron.console.commons.constant.ResponseEnum;
import com.iflytek.astron.console.commons.data.UserInfoDataService;
import com.iflytek.astron.console.commons.entity.user.UserInfo;
import com.iflytek.astron.console.commons.exception.BusinessException;
import com.iflytek.astron.console.hub.config.security.RestfulAccessDeniedHandler;
import com.iflytek.astron.console.hub.config.security.RestfulAuthenticationEntryPoint;
import com.iflytek.astron.console.hub.config.security.SandboxRuntimeCredentialAuthenticationFilter;
import com.iflytek.astron.console.toolkit.controller.skill.SkillSandboxConfigController;
import com.iflytek.astron.console.toolkit.entity.dto.skill.SkillSandboxRuntimeCredentialDto;
import com.iflytek.astron.console.toolkit.security.ArtifactUploadTokenProvider;
import com.iflytek.astron.console.toolkit.security.SandboxRuntimeCredentialTokenProvider;
import com.iflytek.astron.console.toolkit.service.skill.SkillSandboxConfigService;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.boot.test.mock.mockito.MockBean;
import org.springframework.context.annotation.Import;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.security.oauth2.jwt.JwtDecoder;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.MvcResult;

@WebMvcTest(
        controllers = SkillSandboxConfigController.class,
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
class SandboxRuntimeCredentialSecurityConfigTest {

    private static final String VALID_TOKEN = "abcdef0123456789abcdef0123456789";

    @Autowired
    private MockMvc mockMvc;

    @MockBean
    private SkillSandboxConfigService skillSandboxConfigService;

    @MockBean
    private UserInfoDataService userInfoDataService;

    @MockBean
    private JwtDecoder jwtDecoder;

    @MockBean
    private ArtifactUploadTokenProvider artifactUploadTokenProvider;

    @MockBean
    private SandboxRuntimeCredentialTokenProvider sandboxRuntimeCredentialTokenProvider;

    @Test
    void rejectsCredentialRequestWithoutServiceToken() throws Exception {
        mockMvc.perform(get("/skill-sandbox/internal-runtime-config")
                .param("flowId", "flow-1")
                .param("uid", "user-1"))
                .andExpect(status().isUnauthorized());

        verify(skillSandboxConfigService, never())
                .getRuntimeCredential(any(), eq("flow-1"), eq("user-1"), isNull());
    }

    @Test
    void rejectsCredentialRequestWithWrongServiceToken() throws Exception {
        mockMvc.perform(get("/skill-sandbox/internal-runtime-config")
                .header(
                        SandboxRuntimeCredentialAuthenticationFilter.TOKEN_HEADER,
                        "wrong-token")
                .param("flowId", "flow-1")
                .param("uid", "user-1"))
                .andExpect(status().isUnauthorized());

        verify(skillSandboxConfigService, never())
                .getRuntimeCredential(any(), eq("flow-1"), eq("user-1"), isNull());
    }

    @Test
    void returnsOnlyCredentialForValidServiceToken() throws Exception {
        when(sandboxRuntimeCredentialTokenProvider.matches(VALID_TOKEN)).thenReturn(true);
        when(skillSandboxConfigService.getRuntimeCredential(
                        VALID_TOKEN, "flow-1", "user-1", null))
                .thenReturn(new SkillSandboxRuntimeCredentialDto(
                        "e2b", "e2b-secret", 60, false));

        mockMvc.perform(get("/skill-sandbox/internal-runtime-config")
                        .header(
                                SandboxRuntimeCredentialAuthenticationFilter.TOKEN_HEADER,
                                VALID_TOKEN)
                        .param("flowId", "flow-1")
                        .param("uid", "user-1"))
                .andExpect(status().isOk())
                .andExpect(header().string("Cache-Control", "no-store"))
                .andExpect(jsonPath("$.data.apiKey").value("e2b-secret"))
                .andExpect(jsonPath("$.data.provider").value("e2b"))
                .andExpect(jsonPath("$.data.timeoutSeconds").value(60))
                .andExpect(jsonPath("$.data.allowInternetAccess").value(false));

        verify(skillSandboxConfigService)
                .getRuntimeCredential(VALID_TOKEN, "flow-1", "user-1", null);
    }

    @Test
    void jwtOnlyTrailingSlashVariantNeverReturnsRuntimeCredential() throws Exception {
        Instant now = Instant.now();
        Jwt jwt = Jwt.withTokenValue("jwt-only")
                .header("alg", "none")
                .subject("jwt-user")
                .issuedAt(now)
                .expiresAt(now.plusSeconds(300))
                .build();
        when(jwtDecoder.decode("jwt-only")).thenReturn(jwt);
        when(userInfoDataService.createOrGetUser(any(UserInfo.class)))
                .thenAnswer(invocation -> invocation.getArgument(0));
        when(skillSandboxConfigService.getRuntimeCredential(
                isNull(), eq("flow-1"), eq("user-1"), isNull()))
                .thenThrow(new BusinessException(ResponseEnum.UNAUTHORIZED));

        MvcResult result = mockMvc.perform(get("/skill-sandbox/internal-runtime-config/")
                .header("Authorization", "Bearer jwt-only")
                .param("flowId", "flow-1")
                .param("uid", "user-1"))
                .andReturn();

        assertThat(result.getResponse().getContentAsString())
                .doesNotContain("e2b-secret", "\"apiKey\":\"");
    }
}
