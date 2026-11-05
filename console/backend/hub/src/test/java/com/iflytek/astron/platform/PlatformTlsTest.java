package com.iflytek.astron.platform;

import static org.junit.jupiter.api.Assertions.*;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.KeyStore;
import java.time.Duration;
import java.util.Comparator;
import java.util.Map;
import java.util.concurrent.TimeUnit;
import javax.net.ssl.SSLContext;
import javax.net.ssl.SSLHandshakeException;
import javax.net.ssl.TrustManagerFactory;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.Test;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.mock.mockito.MockBean;
import org.springframework.boot.test.web.server.LocalServerPort;
import org.springframework.context.ApplicationContextInitializer;
import org.springframework.context.ConfigurableApplicationContext;
import org.springframework.core.env.SystemEnvironmentPropertySource;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.security.oauth2.jwt.JwtDecoder;
import org.springframework.test.annotation.DirtiesContext;
import org.springframework.test.context.ContextConfiguration;

/** Actual TLS connector and environment-property binding; DB/JWT are explicit fixtures. */
@SpringBootTest(classes=PlatformApplication.class, webEnvironment=SpringBootTest.WebEnvironment.RANDOM_PORT, properties={
        "spring.config.name=platform", "spring.flyway.enabled=false",
        "PLATFORM_JDBC_URL=jdbc:mysql://127.0.0.1:1/unused", "PLATFORM_DB_USER=fixture", "PLATFORM_DB_PASSWORD=fixture",
        "PLATFORM_OIDC_ISSUER=https://issuer.invalid", "PLATFORM_OIDC_JWKS=https://issuer.invalid/jwks",
        "PLATFORM_OIDC_AUDIENCE=open-agent", "PLATFORM_S3_ENDPOINT=http://127.0.0.1:19000",
        "PLATFORM_S3_ACCESS_KEY=fixture-access", "PLATFORM_S3_SECRET_KEY=fixture-secret", "PLATFORM_S3_BUCKET=fixture"
})
@ContextConfiguration(initializers=PlatformTlsTest.TlsEnvironment.class)
@DirtiesContext(classMode=DirtiesContext.ClassMode.AFTER_CLASS)
class PlatformTlsTest {
    private static final String PASSWORD = "synthetic-tls-fixture-only";
    private static final Path DIRECTORY = certificate();
    @LocalServerPort int port;
    @MockBean JdbcTemplate jdbc;
    @MockBean JwtDecoder decoder;

    static class TlsEnvironment implements ApplicationContextInitializer<ConfigurableApplicationContext> {
        @Override public void initialize(ConfigurableApplicationContext context) {
            // Use the same environment variable names and property source as the chart.
            context.getEnvironment().getPropertySources().addFirst(new SystemEnvironmentPropertySource("fixture-tls",
                    Map.of("SERVER_SSL_ENABLED", "true", "SERVER_SSL_KEY_STORE", DIRECTORY.resolve("tls.p12").toUri().toString(),
                            "SERVER_SSL_KEY_STORE_TYPE", "PKCS12", "SERVER_SSL_KEY_STORE_PASSWORD", PASSWORD)));
        }
    }

    private static Path certificate() {
        try {
            Path directory = Files.createTempDirectory("open-agent-tls-fixture-").toRealPath();
            String executable = System.getProperty("os.name").startsWith("Windows") ? "keytool.exe" : "keytool";
            var process = new ProcessBuilder(Path.of(System.getProperty("java.home"), "bin", executable).toString(),
                    "-genkeypair", "-alias", "fixture", "-keyalg", "RSA", "-keysize", "2048", "-validity", "2",
                    "-dname", "CN=localhost", "-ext", "SAN=dns:localhost,ip:127.0.0.1", "-storetype", "PKCS12",
                    "-keystore", directory.resolve("tls.p12").toString(), "-storepass", PASSWORD, "-keypass", PASSWORD,
                    "-noprompt").redirectErrorStream(true).redirectOutput(ProcessBuilder.Redirect.DISCARD).start();
            if (!process.waitFor(30, TimeUnit.SECONDS)) {
                process.destroyForcibly();
                throw new IllegalStateException("TLS fixture keytool timed out");
            }
            if (process.exitValue() != 0) throw new IllegalStateException("TLS fixture keytool failed");
            return directory;
        } catch (Exception error) {
            throw new IllegalStateException("TLS fixture setup failed", error);
        }
    }

    private HttpClient trustedClient() throws Exception {
        var store = KeyStore.getInstance("PKCS12");
        try (var input = Files.newInputStream(DIRECTORY.resolve("tls.p12"))) { store.load(input, PASSWORD.toCharArray()); }
        var trust = TrustManagerFactory.getInstance(TrustManagerFactory.getDefaultAlgorithm());
        trust.init(store);
        var ssl = SSLContext.getInstance("TLS");
        ssl.init(null, trust.getTrustManagers(), null);
        return HttpClient.newBuilder().sslContext(ssl).connectTimeout(Duration.ofSeconds(5)).build();
    }

    private HttpRequest request(String protocol, String path) {
        return HttpRequest.newBuilder(URI.create(protocol + "://127.0.0.1:" + port + path))
                .timeout(Duration.ofSeconds(10)).GET().build();
    }

    @Test void trustedHttpsPreservesHealthAndAuthorizationBoundaries() throws Exception {
        try (var http = trustedClient()) {
            var health = http.send(request("https", "/health/live"), HttpResponse.BodyHandlers.ofString());
            assertEquals(200, health.statusCode());
            assertTrue(health.body().contains("UP"));
            var tenants = http.send(request("https", "/platform/tenants"), HttpResponse.BodyHandlers.ofString());
            assertEquals(401, tenants.statusCode());
            var internal = http.send(request("https", "/internal/platform/reconcile/cluster/test/lookup"), HttpResponse.BodyHandlers.ofString());
            assertTrue(internal.statusCode() == 401 || internal.statusCode() == 403);
        }
    }

    @Test void untrustedCertificateCannotBeBypassed() throws Exception {
        try (var http = HttpClient.newHttpClient()) {
            assertThrows(SSLHandshakeException.class, () -> http.send(request("https", "/health/live"), HttpResponse.BodyHandlers.ofString()));
        }
    }

    @Test void cleartextCannotAccessTheTlsConnector() throws Exception {
        try (var http = HttpClient.newHttpClient()) {
            var response = http.send(request("http", "/health/live"), HttpResponse.BodyHandlers.ofString());
            assertEquals(400, response.statusCode());
            assertFalse(response.body().contains("\"status\":\"UP\""));
        }
    }

    @AfterAll static void removeOnlyOwnedTemporaryCertificate() throws Exception {
        assertTrue(DIRECTORY.getFileName().toString().startsWith("open-agent-tls-fixture-"));
        assertEquals(DIRECTORY, DIRECTORY.toRealPath());
        try (var files = Files.walk(DIRECTORY)) {
            for (Path file : files.sorted(Comparator.reverseOrder()).toList()) {
                assertTrue(file.normalize().startsWith(DIRECTORY));
                Files.delete(file);
            }
        }
    }
}
