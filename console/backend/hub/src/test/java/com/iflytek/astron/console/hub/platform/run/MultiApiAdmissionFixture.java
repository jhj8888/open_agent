package com.iflytek.astron.console.hub.platform.run;

import static org.junit.jupiter.api.Assertions.*;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.nimbusds.jose.JWSAlgorithm;
import com.nimbusds.jose.JWSHeader;
import com.nimbusds.jose.crypto.RSASSASigner;
import com.nimbusds.jose.jwk.JWKSet;
import com.nimbusds.jose.jwk.RSAKey;
import com.nimbusds.jose.jwk.gen.RSAKeyGenerator;
import com.nimbusds.jwt.JWTClaimsSet;
import com.nimbusds.jwt.SignedJWT;
import com.sun.net.httpserver.HttpServer;
import java.net.InetSocketAddress;
import java.net.ServerSocket;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Date;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.Callable;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.function.Consumer;
import java.util.jar.JarFile;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.DriverManagerDataSource;

/** Two packaged application JVMs, real HTTP/JWT/service authentication and one owned MySQL schema.
 * No model, Temporal worker or physical sandbox runs in this admission-only fixture.
 */
final class MultiApiAdmissionFixture implements AutoCloseable {
    private final ObjectMapper json = new ObjectMapper();
    private final HttpClient http = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(5))
            .followRedirects(HttpClient.Redirect.NEVER).build();
    private final JdbcTemplate jdbc;
    private final Map<String, String> environment = new HashMap<>();
    private final Path directory;
    private final Path jar;
    private final HttpServer issuer;
    private final RSAKey signingKey;
    private final String workerSecret = UUID.randomUUID().toString();
    private final String dispatcherSecret = UUID.randomUUID().toString();
    private final List<Process> owned = new ArrayList<>();
    private final List<Map<String, Object>> evidence = new ArrayList<>();
    private final long started = System.nanoTime();
    private Process first;
    private int firstPort, secondPort;
    private String alice, bob;
    private boolean verified;

    MultiApiAdmissionFixture(DriverManagerDataSource source, JdbcTemplate jdbc) throws Exception {
        this.jdbc = jdbc;
        jar = Path.of(System.getenv("OA_RUN_MULTI_API_JAR")).toAbsolutePath();
        try (var archive = new JarFile(jar.toFile())) {
            assertEquals("com.iflytek.astron.platform.PlatformApplication",
                    archive.getManifest().getMainAttributes().getValue("Start-Class"));
            assertNotNull(archive.getEntry("BOOT-INF/classes/db/migration/V1.69__preserve_deleted_requests_after_restore.sql"));
        }
        directory = Files.createTempDirectory("oa-multi-api-");
        signingKey = new RSAKeyGenerator(2048).keyID(UUID.randomUUID().toString()).generate();
        issuer = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        byte[] jwks = new JWKSet(signingKey.toPublicJWK()).toString().getBytes(StandardCharsets.UTF_8);
        issuer.createContext("/jwks", exchange -> {
            exchange.getResponseHeaders().set("Content-Type", "application/json");
            exchange.sendResponseHeaders(200, jwks.length);
            try (var body = exchange.getResponseBody()) { body.write(jwks); }
            finally { exchange.close(); }
        });
        String origin = "http://127.0.0.1:" + issuer.getAddress().getPort();
        // Do not inherit deployment credentials, Spring overrides or JAVA_TOOL_OPTIONS.
        for (String name : List.of("SystemRoot", "WINDIR", "SystemDrive", "TEMP", "TMP", "PATH")) {
            if (System.getenv(name) != null) environment.put(name, System.getenv(name));
        }
        environment.putAll(Map.of(
                "PLATFORM_BIND_ADDRESS", "127.0.0.1", "PLATFORM_JDBC_URL", source.getUrl(),
                "PLATFORM_DB_USER", source.getUsername(), "PLATFORM_DB_PASSWORD", source.getPassword(),
                "PLATFORM_FLYWAY_ENABLED", "false", "PLATFORM_OIDC_ISSUER", origin,
                "PLATFORM_OIDC_JWKS", origin + "/jwks", "PLATFORM_OIDC_AUDIENCE", "admission-fixture",
                "PLATFORM_RUN_ADMISSION_ENABLED", "true", "PLATFORM_RUN_ADMISSION_ENVIRONMENTS", "test,prod"));
        environment.putAll(Map.of("PLATFORM_S3_ENDPOINT", "http://127.0.0.1:9",
                "PLATFORM_S3_ACCESS_KEY", "fixture-access", "PLATFORM_S3_SECRET_KEY", UUID.randomUUID().toString(),
                "PLATFORM_S3_BUCKET", "admission-fixture", "SPRING_DATASOURCE_HIKARI_MAXIMUM_POOL_SIZE", "4"));
        environment.put("PLATFORM_SERVICE_KEYS", json.writeValueAsString(Map.of(
                "fixture-worker", Map.of("role", "worker", "secret", workerSecret),
                "fixture-dispatcher", Map.of("role", "dispatcher", "secret", dispatcherSecret))));
        alice = token("alice", origin, "admission-fixture");
        bob = token("bob", origin, "admission-fixture");
    }

    void verifyDispatch(String deployment) throws Exception {
        issuer.start(); firstPort = port(); first = start(firstPort); ready(first, firstPort);
        String route = "/api/v1/agent-deployments/" + deployment + "/runs";
        String input = "{\"input\":{\"message\":\"Synthetic dispatch fixture\"},\"timeoutSeconds\":3600}";
        // Fail the outbox write inside the real public submission transaction.
        jdbc.execute("CREATE TRIGGER fixture_reject_outbox BEFORE INSERT ON oa_run_outbox FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='fixture failure before commit'");
        try {
            expect(call(firstPort, "POST", route, alice, "transaction-retry", input, false), 503);
            assertEquals(0, count("oa_agent_run")); assertEquals(0, count("oa_run_outbox"));
        } finally { jdbc.execute("DROP TRIGGER fixture_reject_outbox"); }
        // Hold the owned outbox's insertion gap, observe the API transaction's
        // real InnoDB lock wait, then kill the JVM before it can commit.
        try (var blocker = jdbc.getDataSource().getConnection(); var pool = Executors.newSingleThreadExecutor()) {
            blocker.setTransactionIsolation(java.sql.Connection.TRANSACTION_REPEATABLE_READ);
            blocker.setAutoCommit(false);
            try (var statement = blocker.createStatement(); var rows = statement.executeQuery("SELECT id FROM oa_run_outbox WHERE id>=0 FOR UPDATE")) {
                assertFalse(rows.next());
                var pending = pool.submit(() -> call(firstPort, "POST", route, alice, "transaction-retry", input, false));
                boolean waiting = false;
                long until = System.nanoTime() + TimeUnit.SECONDS.toNanos(8);
                while (System.nanoTime() < until) {
                    int waits = jdbc.queryForObject("""
                        SELECT COUNT(*) FROM performance_schema.data_lock_waits w
                        JOIN performance_schema.data_locks l ON l.ENGINE_LOCK_ID=w.REQUESTING_ENGINE_LOCK_ID
                        WHERE l.OBJECT_SCHEMA=? AND l.OBJECT_NAME='oa_run_outbox'
                        """, Integer.class, blocker.getCatalog());
                    if (waits > 0) { waiting = true; break; }
                    Thread.sleep(50);
                }
                assertTrue(waiting, "API did not reach the observed precommit outbox lock boundary");
                first.destroyForcibly(); assertTrue(first.waitFor(30, TimeUnit.SECONDS));
                var lost = assertThrows(java.util.concurrent.ExecutionException.class, () -> pending.get(10, TimeUnit.SECONDS));
                assertInstanceOf(java.io.IOException.class, lost.getCause());
            } finally { blocker.rollback(); }
        }
        first = start(firstPort); ready(first, firstPort);
        assertEquals(0, count("oa_agent_run")); assertEquals(0, count("oa_run_outbox"));
        record("api_crash_before_sql_commit", Map.of("innodb_lock_wait_observed", true, "runs", 0, "outbox", 0));
        String run = runId(call(firstPort, "POST", route, alice, "transaction-retry", input, false));
        assertEquals(1, count("oa_agent_run")); assertEquals(1, count("oa_run_outbox"));
        first.destroyForcibly(); assertTrue(first.waitFor(30, TimeUnit.SECONDS));
        first = start(firstPort); ready(first, firstPort);
        assertEquals(run, runId(call(firstPort, "POST", route, alice, "transaction-retry", input, false)));
        record("sql_commit_and_api_crash", Map.of("rollback_runs", 0, "rollback_outbox", 0, "committed_runs", 1, "committed_outbox", 1));

        Path script = Path.of(System.getenv("OA_RUN_DISPATCH_SCRIPT")).toAbsolutePath();
        Path python = Path.of(System.getenv("OA_RUN_DISPATCH_PYTHON")).toAbsolutePath();
        Path temporal = Path.of(System.getenv("TEMPORAL_TEST_BINARY")).toAbsolutePath();
        assertTrue(Files.isRegularFile(script)); assertTrue(Files.isRegularFile(python)); assertTrue(Files.isRegularFile(temporal));
        var builder = new ProcessBuilder(python.toString(), script.toString());
        builder.environment().clear(); builder.environment().putAll(environment);
        builder.environment().put("PYTHONUTF8", "1");
        builder.environment().put("PYTHONPATH", script.getParent().getParent().getParent().resolve("core").toString());
        builder.directory(directory.toFile()).redirectErrorStream(true).redirectOutput(directory.resolve("dispatch.log").toFile());
        Process verifier = builder.start(); owned.add(verifier);
        try (var inputStream = verifier.getOutputStream()) {
            inputStream.write(json.writeValueAsBytes(Map.of("api", "http://127.0.0.1:" + firstPort,
                    "token", alice, "deployment", deployment, "first_run", run,
                    "worker_key", workerSecret, "dispatcher_key", dispatcherSecret,
                    "temporal_binary", temporal.toString(), "directory", directory.toString())));
        }
        boolean ended = verifier.waitFor(330, TimeUnit.SECONDS);
        if (!ended) {
            // Only this fixture's own descendants, including its private Temporal CLI.
            var descendants = verifier.descendants().toList();
            descendants.forEach(ProcessHandle::destroyForcibly); verifier.destroyForcibly();
            verifier.waitFor(10, TimeUnit.SECONDS);
        }
        assertTrue(ended, "Dispatch fixture timed out; private diagnostics: " + directory);
        assertEquals(0, verifier.exitValue(), "Dispatch fixture failed; private diagnostics: " + directory);
        JsonNode report = json.readTree(directory.resolve("dispatch-result.json").toFile());
        assertTrue(report.path("verified").asBoolean());
        assertEquals(4, count("oa_agent_run")); assertEquals(4, count("oa_run_outbox"));
        assertEquals(4, jdbc.queryForObject("SELECT COUNT(*) FROM oa_run_outbox WHERE state='delivered'", Integer.class));
        assertEquals(3, jdbc.queryForObject("SELECT COUNT(*) FROM oa_run_attempt", Integer.class));
        assertEquals(3, jdbc.queryForObject("SELECT COUNT(*) FROM oa_run_attempt WHERE lease_state='cleanup_pending'", Integer.class));
        assertEquals(3, jdbc.queryForObject("SELECT COUNT(*) FROM oa_run_event WHERE event_key='worker:fixture-effect'", Integer.class));
        assertEquals(3, jdbc.queryForObject("SELECT COUNT(*) FROM oa_agent_run WHERE state='succeeded' AND attempt_no=1", Integer.class));
        assertEquals(1, jdbc.queryForObject("SELECT COUNT(*) FROM oa_agent_run WHERE state='cancelled' AND attempt_no=0", Integer.class));
        assertEquals(List.of(1,1,2,2), jdbc.queryForList("SELECT delivery_attempts FROM oa_run_outbox ORDER BY delivery_attempts", Integer.class));
        System.out.println("DISPATCH_RECOVERY_EVIDENCE " + report);
        record("actual_sql_http_temporal_delivery", Map.of("delivered_outbox", 4, "attempts", 3, "effect_events", 3, "unreleased_fixture_attempts", 3));
        verified = true;
    }

    void verify(String deployment, String other, String production, Consumer<RunLedger.Lease> release) throws Exception {
        issuer.start();
        firstPort = port(); first = start(firstPort);
        secondPort = port(); Process second = start(secondPort);
        assertNotEquals(first.pid(), second.pid());
        ready(first, firstPort); ready(second, secondPort);
        record("independent_processes_ready", Map.of("count", 2));
        String route = "/api/v1/agent-deployments/" + deployment + "/runs";
        String input = "{\"input\":{\"message\":\"Synthetic admission fixture\"},\"timeoutSeconds\":3600}";

        for (int port : List.of(firstPort, secondPort)) {
            expect(call(port, "POST", route, null, "no-auth", input, false), 401);
            expect(call(port, "POST", route, token("alice", environment.get("PLATFORM_OIDC_ISSUER"), "wrong-audience"),
                    "wrong-audience", input, false), 401);
        }
        assertEquals(0, count("oa_agent_run"));
        record("http_authentication", Map.of("unauthenticated_and_wrong_audience_rejected", 4));
        var duplicate = race(12, i -> submit(i, route, alice, "same-key", input));
        Set<String> ids = new java.util.HashSet<>(); int reused = 0;
        for (var response : duplicate) {
            expect(response, 202); ids.add(body(response).path("data").path("run_id").asText());
            if (body(response).path("data").path("reused").asBoolean()) reused++;
        }
        assertEquals(1, ids.size()); assertEquals(11, reused);
        String original = ids.iterator().next(); UUID.fromString(original);
        assertEquals(1, count("oa_agent_run")); assertEquals(1, count("oa_run_outbox"));
        expect(submit(1, route, alice, "same-key", input.replace("Synthetic", "Changed")), 409);
        expect(submit(0, route, bob, "same-key", input), 404);
        record("idempotent_submissions", Map.of("requests", 12, "runs", 1, "outbox", 1, "reused", reused));

        var unique = race(12, i -> submit(i, route, alice, "unique-" + i, input));
        int accepted = 0, limited = 0;
        for (var response : unique) {
            if (response.statusCode() == 202) { accepted++; }
            else {
                expect(response, 429); assertEquals("queue_full", body(response).path("message").asText());
                assertEquals("5", response.headers().firstValue("Retry-After").orElseThrow());
                assertEquals("no-store", response.headers().firstValue("Cache-Control").orElseThrow()); limited++;
            }
        }
        assertEquals(5, accepted); assertEquals(7, limited);
        assertEquals(6, count("oa_agent_run")); assertEquals(6, count("oa_run_outbox"));
        // Identical retries still work when the queue is full.
        expect(submit(1, route, alice, "same-key", input), 202);
        record("shared_queue_backpressure", Map.of("limit", 6, "unique_requests", 12,
                "accepted", accepted, "limited", limited, "retry_after_seconds", 5));

        expect(call(secondPort, "POST", "/api/v1/runs/" + original + "/cancel", alice, null, "{}", false), 200);
        String replacement = runId(submit(0, route, alice, "replacement", input));
        assertEquals(6, queued()); assertEquals(7, count("oa_agent_run"));
        assertEquals("cancelled", body(call(firstPort, "GET", "/api/v1/runs/" + original, alice, null, null, false))
                .path("data").path("state").asText());
        record("queue_cancel_reopens_one_slot", Map.of("queued", queued()));

        // A full tenant/test queue does not consume another tenant's or prod's queue.
        String otherRun = runId(submit(0, "/api/v1/agent-deployments/" + other + "/runs", bob, "same-key", input));
        String prodRun = runId(submit(1, "/api/v1/agent-deployments/" + production + "/runs", alice, "same-key", input));
        expect(call(secondPort, "GET", "/api/v1/runs/" + otherRun, alice, null, null, false), 404);

        var queued = jdbc.queryForList("SELECT run_id FROM oa_agent_run WHERE tenant_id='space-10' AND environment='test' AND state='queued' ORDER BY run_id", String.class);
        var claims = race(queued.size(), i -> claim(i, queued.get(i), "space-10", "test"));
        var leases = new ArrayList<RunLedger.Lease>();
        for (var response : claims) {
            expect(response, 200); JsonNode lease = body(response).path("lease");
            if (!lease.isNull()) leases.add(json.treeToValue(lease, RunLedger.Lease.class));
        }
        assertEquals(2, leases.size()); assertEquals(2, active());
        for (var lease : leases) {
            assertTrue(body(claim(1, lease.runId(), "space-10", "test")).path("lease").isNull());
            expect(call(secondPort, "POST", "/internal/platform/worker/runs/" + lease.runId() + "/finish", null, null,
                    json.writeValueAsString(Map.of("lease", Map.of("tenant", "space-10", "environment", "test", "attempt", lease.attempt(),
                            "leaseId", lease.leaseId(), "fencingToken", lease.fencingToken()), "state", "succeeded")), true), 200);
        }
        assertEquals(2, active());
        assertEquals(2, jdbc.queryForObject("SELECT COUNT(*) FROM oa_run_attempt WHERE lease_state='cleanup_pending'", Integer.class));
        for (var run : queued) assertTrue(body(claim(0, run, "space-10", "test")).path("lease").isNull());
        assertFalse(body(claim(0, otherRun, "space-20", "test")).path("lease").isNull());
        assertFalse(body(claim(1, prodRun, "space-10", "prod")).path("lease").isNull());
        assertEquals(2, active());
        record("shared_execution_slots", Map.of("competing_runs", queued.size(), "limit", 2,
                "claimed", leases.size(), "cleanup_pending_still_counted", 2, "independent_scopes_claimed", 2));

        // These fixture claims never start executors or sandboxes. Invoke the existing
        // trusted ledger release boundary to test readmission, not physical cleanup.
        release.accept(leases.getFirst());
        var readmission = race(queued.size(), i -> claim(i, queued.get(i), "space-10", "test"));
        int reclaimed = 0;
        for (var response : readmission) { expect(response, 200); if (!body(response).path("lease").isNull()) reclaimed++; }
        assertEquals(1, reclaimed); assertEquals(2, active());
        record("release_reopens_one_execution_slot", Map.of("new_claims", reclaimed, "active", active()));

        // Real process death and replacement, not rebuilding a service object in one JVM.
        first.destroyForcibly(); assertTrue(first.waitFor(30, TimeUnit.SECONDS));
        expect(call(secondPort, "GET", "/api/v1/runs/" + replacement, alice, null, null, false), 200);
        first = start(firstPort); ready(first, firstPort);
        var retry = submit(0, route, alice, "replacement", input);
        assertEquals(replacement, runId(retry)); assertTrue(body(retry).path("data").path("reused").asBoolean());
        assertEquals(2, active());
        for (var run : queued) assertTrue(body(claim(0, run, "space-10", "test")).path("lease").isNull());
        record("api_process_crash_and_restart", Map.of("same_run", true, "active_slots_preserved", 2));
        assertEquals(9, count("oa_agent_run")); assertEquals(9, count("oa_run_outbox"));
        verified = true;
    }

    private int active() { return jdbc.queryForObject("SELECT COUNT(*) FROM oa_run_attempt WHERE tenant_id='space-10' AND environment='test' AND lease_state<>'released'", Integer.class); }
    private int queued() { return jdbc.queryForObject("SELECT COUNT(*) FROM oa_agent_run WHERE tenant_id='space-10' AND environment='test' AND state='queued'", Integer.class); }
    private int count(String table) {
        assertTrue(Set.of("oa_agent_run", "oa_run_outbox").contains(table));
        return jdbc.queryForObject("SELECT COUNT(*) FROM " + table, Integer.class);
    }
    private void record(String stage, Map<String, Object> values) { evidence.add(Map.of("stage", stage, "result", "passed", "details", values)); }
    private JsonNode body(HttpResponse<String> response) throws Exception { return json.readTree(response.body()); }
    private void expect(HttpResponse<String> response, int status) { assertEquals(status, response.statusCode(), "HTTP contract status"); }
    private String runId(HttpResponse<String> response) throws Exception {
        expect(response, 202); String id = body(response).path("data").path("run_id").asText(); UUID.fromString(id); return id;
    }
    private HttpResponse<String> submit(int i, String route, String token, String key, String input) throws Exception {
        return call(i % 2 == 0 ? firstPort : secondPort, "POST", route, token, key, input, false);
    }
    private HttpResponse<String> claim(int i, String run, String tenant, String env) throws Exception {
        return call(i % 2 == 0 ? firstPort : secondPort, "POST", "/internal/platform/worker/runs/" + run + "/claim",
                null, null, json.writeValueAsString(Map.of("tenant", tenant, "environment", env)), true);
    }
    private HttpResponse<String> call(int port, String method, String route, String token, String key, String body, boolean worker) throws Exception {
        var request = HttpRequest.newBuilder(URI.create("http://127.0.0.1:" + port + route)).timeout(Duration.ofSeconds(60))
                .header("Accept", "application/json");
        if (token != null) request.header("Authorization", "Bearer " + token);
        if (key != null) request.header("Idempotency-Key", key);
        if (worker) request.header("X-Platform-Key-Id", "fixture-worker").header("X-Platform-Service-Key", workerSecret);
        if (body != null) request.header("Content-Type", "application/json");
        request.method(method, body == null ? HttpRequest.BodyPublishers.noBody() : HttpRequest.BodyPublishers.ofString(body));
        return http.send(request.build(), HttpResponse.BodyHandlers.ofString());
    }
    @FunctionalInterface private interface Request { HttpResponse<String> run(int i) throws Exception; }
    private List<HttpResponse<String>> race(int count, Request request) throws Exception {
        try (var pool = Executors.newFixedThreadPool(count)) {
            var start = new CountDownLatch(1); var ready = new CountDownLatch(count);
            var pending = new ArrayList<java.util.concurrent.Future<HttpResponse<String>>>();
            for (int i = 0; i < count; i++) {
                final int index = i;
                pending.add(pool.submit((Callable<HttpResponse<String>>) () -> { ready.countDown(); assertTrue(start.await(10, TimeUnit.SECONDS)); return request.run(index); }));
            }
            assertTrue(ready.await(10, TimeUnit.SECONDS)); start.countDown();
            var results = new ArrayList<HttpResponse<String>>();
            for (var future : pending) results.add(future.get(90, TimeUnit.SECONDS));
            return results;
        }
    }
    private String token(String subject, String origin, String audience) throws Exception {
        var jwt = new SignedJWT(new JWSHeader.Builder(JWSAlgorithm.RS256).keyID(signingKey.getKeyID()).build(),
                new JWTClaimsSet.Builder().subject(subject).issuer(origin).audience(audience)
                        .issueTime(new Date()).expirationTime(Date.from(Instant.now().plusSeconds(1200))).build());
        jwt.sign(new RSASSASigner(signingKey)); return jwt.serialize();
    }
    private int port() throws Exception {
        try (var socket = new ServerSocket()) { socket.bind(new InetSocketAddress("127.0.0.1", 0)); return socket.getLocalPort(); }
    }
    private Process start(int port) throws Exception {
        String executable = System.getProperty("os.name").startsWith("Windows") ? "java.exe" : "java";
        var builder = new ProcessBuilder(Path.of(System.getProperty("java.home"), "bin", executable).toString(),
                "-Xms64m", "-Xmx384m", "-jar", jar.toString());
        builder.environment().clear(); builder.environment().putAll(environment);
        builder.environment().put("PLATFORM_PORT", Integer.toString(port));
        builder.directory(directory.toFile()).redirectErrorStream(true)
                .redirectOutput(directory.resolve("api-" + owned.size() + ".log").toFile());
        Process process = builder.start(); owned.add(process); return process;
    }
    private void ready(Process process, int port) throws Exception {
        long deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(120);
        while (System.nanoTime() < deadline) {
            assertTrue(process.isAlive(), "API process exited; inspect private fixture logs: " + directory);
            try { if (call(port, "GET", "/health/ready", null, null, null, false).statusCode() == 200) return; }
            catch (java.io.IOException unavailable) { /* startup only */ }
            Thread.sleep(200);
        }
        fail("API readiness timed out; inspect private fixture logs: " + directory);
    }
    @Override public void close() throws Exception {
        boolean stopped = true;
        for (var process : owned) {
            if (process.isAlive()) process.destroy();
            if (!process.waitFor(10, TimeUnit.SECONDS)) { process.destroyForcibly(); stopped &= process.waitFor(10, TimeUnit.SECONDS); }
        }
        issuer.stop(0); http.close();
        assertTrue(stopped, "Owned child API process did not exit");
        // Emit only fixed metrics after cleanup. No JWTs, credentials, SQL or response bodies.
        System.out.println("MULTI_API_EVIDENCE " + json.writeValueAsString(Map.of("stages", evidence,
                "verified", verified, "child_processes_stopped", true, "elapsed_seconds", (System.nanoTime() - started) / 1_000_000_000.0)));
        if (verified) {
            try (var files = Files.list(directory)) {
                for (var file : files.toList()) Files.delete(file);
            }
            Files.delete(directory);
        } else {
            System.out.println("Private fixture diagnostics retained at: " + directory);
        }
    }
}
