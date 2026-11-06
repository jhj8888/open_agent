package com.iflytek.astron.platform;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.*;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;

import com.iflytek.astron.console.commons.util.RequestContextUtil;
import com.iflytek.astron.console.hub.HubApplication;
import com.iflytek.astron.console.hub.platform.TenantGovernanceService;
import com.iflytek.astron.console.hub.platform.run.AgentRunService;
import java.time.Instant;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.mock.mockito.MockBean;
import org.springframework.boot.test.web.server.LocalServerPort;
import org.springframework.context.ApplicationContext;
import org.springframework.dao.DataAccessResourceFailureException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.security.oauth2.jwt.JwtDecoder;
import org.springframework.test.annotation.DirtiesContext;
import org.springframework.test.web.servlet.MockMvc;

/** Full Spring bean graph; database and issuer are explicit fixtures, not a live deployment test. */
@SpringBootTest(classes=PlatformApplication.class, webEnvironment=SpringBootTest.WebEnvironment.RANDOM_PORT, properties={
        "spring.config.name=platform", "spring.flyway.enabled=false",
        "PLATFORM_JDBC_URL=jdbc:mysql://127.0.0.1:1/unused", "PLATFORM_DB_USER=fixture", "PLATFORM_DB_PASSWORD=fixture",
        "PLATFORM_OIDC_ISSUER=https://issuer.invalid", "PLATFORM_OIDC_JWKS=https://issuer.invalid/jwks",
        "PLATFORM_OIDC_AUDIENCE=open-agent", "PLATFORM_S3_ENDPOINT=http://127.0.0.1:19000",
        "PLATFORM_S3_ACCESS_KEY=fixture-access", "PLATFORM_S3_SECRET_KEY=fixture-secret", "PLATFORM_S3_BUCKET=fixture"
})
@AutoConfigureMockMvc
@DirtiesContext(classMode=DirtiesContext.ClassMode.AFTER_CLASS)
class PlatformApplicationTest {
    @Autowired ApplicationContext application;
    @Autowired MockMvc mvc;
    @LocalServerPort int port;
    @MockBean JdbcTemplate jdbc;
    @MockBean JwtDecoder decoder;
    @MockBean TenantGovernanceService governance;
    @MockBean com.iflytek.astron.console.hub.platform.CurrentProfileService profiles;
    @org.springframework.boot.test.mock.mockito.SpyBean
    com.iflytek.astron.console.hub.platform.run.TaskHistoryService history;

    @Test void taskHistoryUsesJwtManagementScopeAndBoundedQueryContract() throws Exception {
        String path="/platform/tenants/space-10/environments/test/runs";
        mvc.perform(get(path).header("X-User-Id","forged")).andExpect(status().isUnauthorized());
        mvc.perform(get(path).header("X-Platform-Key-Id","worker").header("X-Platform-Service-Key","w".repeat(32)))
                .andExpect(status().isUnauthorized());
        when(decoder.decode("opaque-deployment-key")).thenThrow(new org.springframework.security.oauth2.jwt.BadJwtException("fixture"));
        mvc.perform(get(path).header("Authorization","Bearer opaque-deployment-key")).andExpect(status().isUnauthorized());
        when(decoder.decode("history-jwt")).thenReturn(Jwt.withTokenValue("history-jwt").header("alg","RS256")
                .subject("verified-history-user").issuedAt(Instant.now()).expiresAt(Instant.now().plusSeconds(60)).build());
        doAnswer(invocation->{
            assertEquals("verified-history-user",RequestContextUtil.getUID());
            assertEquals(new com.iflytek.astron.console.hub.platform.run.RunLedger.Scope("space-10","test"),invocation.getArgument(0));
            assertEquals(50,(int)invocation.getArgument(1));assertNull(invocation.getArgument(2));
            assertEquals("failed",invocation.getArgument(3));assertEquals("报告 100%",invocation.getArgument(4));
            return new com.iflytek.astron.console.hub.platform.run.TaskHistoryService.Page(List.of(),null,false);
        }).when(history).list(any(),anyInt(),any(),anyString(),anyString());
        mvc.perform(get(path).param("state","failed").param("search","报告 100%")
                .header("Authorization","Bearer history-jwt").header("X-User-Id","forged"))
                .andExpect(status().isOk()).andExpect(header().string("Cache-Control","no-store"))
                .andExpect(jsonPath("$.data.items").isArray()).andExpect(jsonPath("$.data.hasMore").value(false));
        mvc.perform(get(path).param("limit","not-a-number").header("Authorization","Bearer history-jwt"))
                .andExpect(status().isBadRequest());
        doThrow(new com.iflytek.astron.console.hub.platform.run.RunFailure("invalid_cursor"))
                .when(history).list(any(),anyInt(),any(),anyString(),anyString());
        mvc.perform(get(path).param("before","bad").header("Authorization","Bearer history-jwt"))
                .andExpect(status().isUnprocessableEntity()).andExpect(jsonPath("$.message").value("invalid_cursor"));
    }

    @Test void profileUsesVerifiedSubjectOnlyAndDoesNotAcceptForgedGrants() throws Exception {
        mvc.perform(post("/platform/profile").header("X-User-Id","forged"))
                .andExpect(status().isUnauthorized());
        verifyNoInteractions(profiles);
        when(decoder.decode("profile-jwt")).thenReturn(Jwt.withTokenValue("profile-jwt").header("alg","RS256")
                .subject("verified-new-user").claim("name","New User")
                .issuedAt(Instant.now()).expiresAt(Instant.now().plusSeconds(60)).build());
        when(profiles.register("verified-new-user","New User")).thenReturn(Map.of("uid","verified-new-user"));
        mvc.perform(post("/platform/profile").header("Authorization","Bearer profile-jwt")
                        .header("X-User-Id","forged").contentType("application/json")
                        .content("{\"uid\":\"forged\",\"role\":\"tenant_admin\",\"tenantId\":\"other\"}"))
                .andExpect(status().isOk()).andExpect(jsonPath("$.data.uid").value("verified-new-user"));
        verify(profiles).register("verified-new-user","New User");
        verifyNoInteractions(governance);
        when(profiles.register("verified-new-user","New User"))
                .thenThrow(new com.iflytek.astron.console.hub.platform.CurrentProfileService.ProfileUnavailable());
        mvc.perform(post("/platform/profile").header("Authorization","Bearer profile-jwt"))
                .andExpect(status().isForbidden()).andExpect(jsonPath("$.message").value("profile_unavailable"));
    }

    @Test void dedicatedContextLoadsRealRunServiceWithoutLegacyApplication() throws Exception {
        assertNotNull(application.getBean(AgentRunService.class));
        assertTrue(application.getBeansOfType(HubApplication.class).isEmpty());
        assertFalse(application.containsBean("redisson"));
        assertFalse(application.containsBean("sqlSessionFactory"));
        mvc.perform(get("/health/live")).andExpect(status().isOk()).andExpect(jsonPath("$.status").value("UP"));
        mvc.perform(get("/workflow/v1/chat/completions")).andExpect(status().is4xxClientError());
    }

    @Test void managementUsesVerifiedSubjectAndRejectsForgedIdentityHeaders() throws Exception {
        mvc.perform(get("/platform/tenants").header("X-User-Id","forged"))
                .andExpect(status().isUnauthorized());
        verifyNoInteractions(governance);
        when(decoder.decode("fixture-jwt")).thenReturn(Jwt.withTokenValue("fixture-jwt").header("alg","RS256")
                .subject("verified-alice").issuedAt(Instant.now()).expiresAt(Instant.now().plusSeconds(60)).build());
        when(governance.tenants()).thenAnswer(invocation -> {
            assertEquals("verified-alice",RequestContextUtil.getUID());
            return List.of(Map.of("tenantId","tenant-a"));
        });
        mvc.perform(get("/platform/tenants").header("Authorization","Bearer fixture-jwt").header("X-User-Id","forged"))
                .andExpect(status().isOk()).andExpect(jsonPath("$.data.tenants[0].tenantId").value("tenant-a"));
    }

    @Test void readinessChecksRequiredSchemaAndDoesNotExposeDatabaseErrors() throws Exception {
        when(jdbc.queryForList("SELECT key_id FROM oa_deployment_credential LIMIT 0"))
                .thenThrow(new DataAccessResourceFailureException("private connection information"));
        mvc.perform(get("/health/ready")).andExpect(status().isServiceUnavailable())
                .andExpect(content().json("{\"status\":\"DOWN\"}"));
    }

    @Test void realServletErrorDispatchPreservesUnauthorizedStatus() throws Exception {
        try (var client=java.net.http.HttpClient.newHttpClient()) {
            var request=java.net.http.HttpRequest.newBuilder(java.net.URI.create("http://127.0.0.1:"+port+"/platform/tenants"))
                    .header("X-User-Id","forged").timeout(java.time.Duration.ofSeconds(10)).GET().build();
            var response=client.send(request,java.net.http.HttpResponse.BodyHandlers.ofString());
            assertEquals(401,response.statusCode());
            verifyNoInteractions(governance);
        }
    }
}
