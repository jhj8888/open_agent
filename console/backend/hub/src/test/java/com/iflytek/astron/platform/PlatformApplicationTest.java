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
