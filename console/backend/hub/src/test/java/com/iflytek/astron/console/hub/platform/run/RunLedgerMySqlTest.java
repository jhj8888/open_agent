package com.iflytek.astron.console.hub.platform.run;

import static org.junit.jupiter.api.Assertions.*;

import com.fasterxml.jackson.databind.ObjectMapper;
import java.nio.charset.StandardCharsets;
import java.sql.Timestamp;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;
import java.util.concurrent.Callable;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.regex.Pattern;
import org.junit.jupiter.api.*;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.springframework.core.io.ClassPathResource;
import org.springframework.core.io.ByteArrayResource;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.DataSourceTransactionManager;
import org.springframework.jdbc.datasource.DriverManagerDataSource;
import org.springframework.jdbc.datasource.init.ResourceDatabasePopulator;

/** Real MySQL only. Each case owns a fresh schema; credentials come solely from the environment. */
@EnabledIfEnvironmentVariable(named = "OA_RUN_TEST_JDBC_URL", matches = ".+")
class RunLedgerMySqlTest {
    private static final String A = "space-10", B = "space-20";
    private final ObjectMapper json = new ObjectMapper();
    private final RunLedger.Scope scope = new RunLedger.Scope(A, "test");
    private DriverManagerDataSource source;
    private JdbcTemplate admin, jdbc;
    private RunLedger ledger;
    private String database, deployment;

    @BeforeEach void setup() throws Exception {
        String base = System.getenv("OA_RUN_TEST_JDBC_URL");
        assertTrue(base.startsWith("jdbc:mysql://127.0.0.1:"), "Use an isolated loopback test database");
        admin = new JdbcTemplate(dataSource(base));
        database = "oa_run_verify_" + UUID.randomUUID().toString().replace("-", "");
        admin.execute("CREATE DATABASE " + database + " CHARACTER SET utf8mb4 COLLATE utf8mb4_bin");
        int query = base.indexOf('?');
        source = dataSource((query < 0 ? base : base.substring(0, query)) + database + (query < 0 ? "" : base.substring(query)));
        jdbc = new JdbcTemplate(source);
        var scripts = new ResourceDatabasePopulator();
        scripts.addScript(table("V1.1__init_core.sql", "user_info"));
        scripts.addScript(table("V1.3__init_space.sql", "agent_space"));
        scripts.addScript(table("V1.3__init_space.sql", "agent_space_user"));
        for (String migration : List.of("V1.49__add_platform_tenant_boundaries.sql", "V1.50__add_scoped_model_connections.sql",
                "V1.51__add_immutable_skill_releases.sql", "V1.52__add_durable_agent_runs.sql"))
            scripts.addScript(new ClassPathResource("db/migration/" + migration));
        scripts.execute(source);
        for (String tenant : List.of(A, B)) {
            jdbc.update("INSERT INTO oa_tenant(tenant_id,display_name,scope_kind,space_id) VALUES(?,?,'space',?)", tenant, tenant, tenant.equals(A) ? 10 : 20);
            jdbc.update("INSERT INTO oa_tenant_environment(tenant_id,environment,max_concurrent_runs,max_queued_runs) VALUES(?,'test',2,20),(?,'prod',2,20)", tenant, tenant);
        }
        deployment = seedDeployment(scope);
        ledger = instance();
    }

    @AfterEach void cleanup() {
        if (admin != null && database != null && database.matches("oa_run_verify_[a-f0-9]{32}")) admin.execute("DROP DATABASE " + database);
    }

    private DriverManagerDataSource dataSource(String url) {
        return new DriverManagerDataSource(url, System.getenv("OA_RUN_TEST_DB_USER"), System.getenv("OA_RUN_TEST_DB_PASSWORD"));
    }
    private RunLedger instance() { return new RunLedger(new JdbcTemplate(source), new DataSourceTransactionManager(source), json); }
    private RunOutbox outbox() { return new RunOutbox(new JdbcTemplate(source), new DataSourceTransactionManager(source)); }
    private ByteArrayResource table(String file, String table) throws Exception {
        String sql = new ClassPathResource("db/migration/" + file).getContentAsString(StandardCharsets.UTF_8);
        var match = Pattern.compile("CREATE TABLE `" + table + "`\\s*\\(.*?\\) ENGINE=.*?;", Pattern.DOTALL).matcher(sql);
        assertTrue(match.find());
        return new ByteArrayResource(match.group().getBytes(StandardCharsets.UTF_8));
    }
    private String seedDeployment(RunLedger.Scope target) {
        String model = UUID.randomUUID().toString(), id = UUID.randomUUID().toString();
        jdbc.update("INSERT INTO oa_model_connection(tenant_id,environment,connection_id,display_name,current_version,created_by) VALUES(?,?,?,'Synthetic model',1,'alice')",
                target.tenant(), target.environment(), model);
        jdbc.update("""
                INSERT INTO oa_model_connection_version(tenant_id,environment,connection_id,version,endpoint,model_name,
                    supports_stream,supports_tools,supports_usage,connect_timeout_seconds,read_timeout_seconds,total_timeout_seconds,
                    credential_ref,credential_key_id,credential_nonce,credential_cipher)
                VALUES(?,?,?,1,'https://model.invalid/v1','synthetic',true,true,true,5,60,120,?,'test',?,?)
                """, target.tenant(), target.environment(), model, UUID.randomUUID().toString(), new byte[12], new byte[32]);
        String snapshot = "{\"systemPrompt\":\"Synthetic test only\"}";
        jdbc.update("""
                INSERT INTO oa_agent_deployment(tenant_id,environment,deployment_id,display_name,executor_kind,agent_version,
                    model_connection_id,model_version,snapshot_json,snapshot_sha256,state,created_by)
                VALUES(?,?,?,'Test','deepagents','v1',?,1,?,?,?,'alice')
                """, target.tenant(), target.environment(), id, model, snapshot, RunJson.hash(snapshot), target.environment().equals("test") ? "test" : "published");
        return id;
    }
    private RunLedger.Accepted submit(String key) {
        return ledger.submit(scope, deployment, "alice", key, json.createObjectNode().put("message", "hello"), 3600, () -> {});
    }
    private RunLedger.Lease claim(String id) { return ledger.claim(scope, id, "worker-1").orElseThrow(); }
    private void code(String expected, org.junit.jupiter.api.function.Executable operation) {
        assertEquals(expected, assertThrows(RunFailure.class, operation).code());
    }
    private long count(String table) { return jdbc.queryForObject("SELECT COUNT(*) FROM " + table, Long.class); }
    private String state(String id) { return (String) ledger.get(scope, id).get("state"); }
    private <T> List<T> concurrent(List<Callable<T>> operations) throws Exception {
        try (var executor = Executors.newFixedThreadPool(operations.size())) {
            CountDownLatch ready = new CountDownLatch(operations.size()), start = new CountDownLatch(1);
            var futures = operations.stream().map(operation -> executor.submit(() -> {
                ready.countDown(); assertTrue(start.await(15, TimeUnit.SECONDS)); return operation.call();
            })).toList();
            assertTrue(ready.await(15, TimeUnit.SECONDS)); start.countDown();
            List<T> results = new ArrayList<>();
            for (var future : futures) results.add(future.get(30, TimeUnit.SECONDS));
            return results;
        }
    }

    @Test void migrationRejectsCrossEnvironmentAndInvalidTerminalRows() {
        var id = submit("a").runId();
        assertThrows(org.springframework.dao.DataAccessException.class,
                () -> jdbc.update("UPDATE oa_agent_run SET environment='prod' WHERE run_id=?", id));
        assertThrows(org.springframework.dao.DataAccessException.class,
                () -> jdbc.update("UPDATE oa_agent_run SET state='succeeded' WHERE run_id=?", id));
        assertThrows(org.springframework.dao.DataAccessException.class,
                () -> jdbc.update("UPDATE oa_agent_deployment SET state='published' WHERE deployment_id=?", deployment));
        assertThrows(org.springframework.dao.DataAccessException.class,
                () -> jdbc.update("UPDATE oa_agent_run SET event_floor=99 WHERE run_id=?", id));
    }

    @Test void submissionIsCanonicalIdempotentAndActorBound() throws Exception {
        var first = ledger.submit(scope, deployment, "alice", "same", json.readTree("{\"b\":2,\"a\":1}"), 60, () -> {});
        var second = instance().submit(scope, deployment, "alice", "same", json.readTree("{\"a\":1,\"b\":2}"), 60, () -> {});
        assertEquals(first.runId(), second.runId()); assertTrue(second.reused());
        code("idempotency_conflict", () -> ledger.submit(scope, deployment, "alice", "same", json.readTree("{\"a\":1,\"b\":3}"), 60, () -> {}));
        code("idempotency_conflict", () -> ledger.submit(scope, deployment, "alice", "same", json.readTree("{\"a\":1,\"b\":2}"), 61, () -> {}));
        code("resource_unavailable", () -> ledger.submit(scope, deployment, "bob", "same", json.readTree("{\"a\":1,\"b\":2}"), 60, () -> {}));
        assertEquals(1, count("oa_agent_run")); assertEquals(1, count("oa_run_outbox"));
        assertEquals(1, count("oa_run_event")); assertEquals(1, count("oa_tenant_audit"));
    }

    @Test void concurrentDuplicateSubmissionsCreateOneRunAndOutbox() throws Exception {
        List<Callable<RunLedger.Accepted>> operations = new ArrayList<>();
        for (int i = 0; i < 10; i++) operations.add(() -> instance().submit(scope, deployment, "alice", "same",
                json.createObjectNode(), 60, () -> {}));
        var results = concurrent(operations);
        assertEquals(1, results.stream().map(RunLedger.Accepted::runId).distinct().count());
        assertEquals(1, results.stream().filter(result -> !result.reused()).count());
        assertEquals(1, count("oa_run_outbox"));
    }

    @Test void outboxFailureRollsBackRunEventAuditAndIdempotencyKey() {
        jdbc.execute("CREATE TRIGGER fail_outbox BEFORE INSERT ON oa_run_outbox FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='synthetic failure'");
        assertThrows(org.springframework.dao.DataAccessException.class, () -> submit("fault"));
        assertEquals(0, count("oa_agent_run")); assertEquals(0, count("oa_run_event")); assertEquals(0, count("oa_tenant_audit"));
        jdbc.execute("DROP TRIGGER fail_outbox");
        assertFalse(submit("fault").reused());
    }

    @Test void authorizationIsRecheckedInsideTransactionBeforeDuplicateReturn() {
        submit("a");
        code("revoked", () -> ledger.submit(scope, deployment, "alice", "a", json.createObjectNode(), 60,
                () -> { throw new RunFailure("revoked"); }));
        assertEquals(1, count("oa_agent_run"));
    }

    @Test void concurrentQueueBackpressureDoesNotOvershoot() throws Exception {
        jdbc.update("UPDATE oa_tenant_environment SET max_queued_runs=3 WHERE tenant_id=? AND environment='test'", A);
        List<Callable<String>> operations = new ArrayList<>();
        for (int i = 0; i < 10; i++) {
            String key = "key-" + i;
            operations.add(() -> { try { instance().submit(scope, deployment, "alice", key, json.createObjectNode(), 60, () -> {}); return "accepted"; }
                catch (RunFailure failure) { return failure.code(); } });
        }
        var results = concurrent(operations);
        assertEquals(3, results.stream().filter("accepted"::equals).count());
        assertEquals(7, results.stream().filter("queue_full"::equals).count());
        assertEquals(3, count("oa_run_outbox"));
    }

    @Test void concurrentClaimsAcrossInstancesRespectExecutionQuotaAndDuplicates() throws Exception {
        List<Callable<Optional<RunLedger.Lease>>> operations = new ArrayList<>();
        for (int i = 0; i < 10; i++) {
            String id = submit("run-" + i).runId();
            operations.add(() -> instance().claim(scope, id, "worker"));
        }
        var results = concurrent(operations);
        assertEquals(2, results.stream().filter(Optional::isPresent).count());
        assertEquals(2, count("oa_run_attempt"));
        for (var result : results) if (result.isPresent()) assertTrue(ledger.claim(scope, result.get().runId(), "another").isEmpty());
    }

    @Test void heldAndCleanupPendingSlotsNeverExpireIntoFreeCapacity() {
        jdbc.update("UPDATE oa_tenant_environment SET max_concurrent_runs=1 WHERE tenant_id=? AND environment='test'", A);
        String first = submit("a").runId(), second = submit("b").runId();
        var lease = claim(first);
        jdbc.update("UPDATE oa_run_attempt SET heartbeat_until=TIMESTAMPADD(SECOND,-1,CURRENT_TIMESTAMP(3)) WHERE lease_id=?", lease.leaseId());
        assertFalse(ledger.heartbeat(lease)); assertTrue(ledger.claim(scope, second, "worker").isEmpty());
        var finished = ledger.finish(lease, RunState.SUCCEEDED, null);
        assertEquals("failed", finished.get("state")); assertEquals("outcome_unknown", finished.get("error_code"));
        assertTrue(ledger.claim(scope, second, "worker").isEmpty());
        ledger.resourcesReleased(lease); ledger.resourcesReleased(lease);
        assertTrue(ledger.claim(scope, second, "worker").isPresent());
    }

    @Test void cancellingQueuedRunNeverStartsAnAttempt() {
        String id = submit("a").runId();
        assertEquals("cancelled", ledger.cancel(scope, id, "alice", () -> {}).get("state"));
        assertEquals("cancelled", ledger.cancel(scope, id, "alice", () -> {}).get("state"));
        assertTrue(ledger.claim(scope, id, "worker").isEmpty()); assertEquals(0, count("oa_run_attempt"));
        assertEquals(3, ledger.events(scope, id, 0, 200).events().size());
    }

    @Test void runningCancellationFencesEventsAndConvertsLateSuccessToCancelled() {
        String id = submit("a").runId(); var lease = claim(id);
        assertEquals("cancel_requested", ledger.cancel(scope, id, "alice", () -> {}).get("state"));
        assertFalse(ledger.heartbeat(lease));
        code("lease_fenced", () -> ledger.append(lease, "late", "message.delta", json.createObjectNode()));
        assertEquals("cancelled", ledger.finish(lease, RunState.SUCCEEDED, null).get("state"));
        assertEquals("cancelled", ledger.finish(lease, RunState.FAILED, "execution_failed").get("state"));
        assertEquals(2, count("oa_run_outbox"));
    }

    @Test void successAndCancelRaceHasOneStableTerminalOutcome() throws Exception {
        String id = submit("a").runId(); var lease = claim(id);
        concurrent(List.of(() -> instance().cancel(scope, id, "alice", () -> {}),
                () -> instance().finish(lease, RunState.SUCCEEDED, null)));
        String terminal = state(id);
        assertTrue(List.of("succeeded", "cancelled").contains(terminal));
        assertEquals(terminal, ledger.finish(lease, RunState.FAILED, "execution_failed").get("state"));
        assertEquals(terminal, ledger.cancel(scope, id, "alice", () -> {}).get("state"));
    }

    @Test void wrongScopeAndForgedLeaseAreRejected() {
        String id = submit("a").runId(); var lease = claim(id);
        code("resource_unavailable", () -> ledger.get(new RunLedger.Scope(B, "test"), id));
        code("resource_unavailable", () -> ledger.cancel(new RunLedger.Scope(A, "prod"), id, "alice", () -> {}));
        code("lease_fenced", () -> ledger.heartbeat(new RunLedger.Lease(scope, id, 1, lease.leaseId(), lease.fencingToken() + 1, lease.workerId())));
        code("lease_fenced", () -> ledger.finish(new RunLedger.Lease(scope, id, 1, lease.leaseId(), lease.fencingToken(), "forged"), RunState.SUCCEEDED, null));
        assertEquals("running", state(id));
        jdbc.update("UPDATE oa_agent_run SET attempt_no=2 WHERE run_id=?", id);
        code("lease_fenced", () -> ledger.finish(lease, RunState.SUCCEEDED, null));
        assertFalse(ledger.heartbeat(lease));
    }

    @Test void deadlinesBeforeAndDuringExecutionNeverBecomeSuccess() {
        String queued = submit("a").runId();
        jdbc.update("UPDATE oa_agent_run SET deadline_at=TIMESTAMPADD(SECOND,-1,CURRENT_TIMESTAMP(3)) WHERE run_id=?", queued);
        assertTrue(ledger.claim(scope, queued, "worker").isEmpty()); assertEquals("timed_out", state(queued));
        String running = submit("b").runId(); var lease = claim(running);
        jdbc.update("UPDATE oa_agent_run SET deadline_at=TIMESTAMPADD(SECOND,-1,CURRENT_TIMESTAMP(3)) WHERE run_id=?", running);
        assertFalse(ledger.heartbeat(lease)); assertEquals("timed_out", ledger.finish(lease, RunState.SUCCEEDED, null).get("state"));
    }

    @Test void revokedModelAndSuspendedTenantFailClosed() {
        String id = submit("a").runId();
        jdbc.update("UPDATE oa_model_connection_version SET revoked=true WHERE tenant_id=?", A);
        code("deployment_unavailable", () -> submit("b"));
        assertTrue(ledger.claim(scope, id, "worker").isEmpty()); assertEquals("failed", state(id));
        jdbc.update("UPDATE oa_model_connection_version SET revoked=false WHERE tenant_id=?", A);
        String running = submit("c").runId(); var lease = claim(running);
        jdbc.update("UPDATE oa_tenant SET status='suspended' WHERE tenant_id=?", A);
        code("tenant_inactive", () -> submit("d")); assertFalse(ledger.heartbeat(lease));
        assertEquals("cancel_requested", ledger.cancel(scope, running, "alice", () -> {}).get("state"));
    }

    @Test void eventsAreOrderedDeduplicatedBoundedAndReconnectable() {
        String id = submit("a").runId(); var lease = claim(id);
        var body = json.createObjectNode().put("text", "hello");
        assertEquals(3, ledger.append(lease, "chunk1", "message.delta", body));
        assertEquals(3, ledger.append(lease, "chunk1", "message.delta", body));
        code("event_conflict", () -> ledger.append(lease, "chunk1", "message.delta", json.createObjectNode().put("text", "different")));
        var page = ledger.events(scope, id, 2, 1);
        assertEquals(1, page.events().size()); assertEquals(3L, page.events().getFirst().get("sequence_no"));
        assertFalse(page.terminal()); assertTrue(ledger.events(scope, id, 3, 10).events().isEmpty());
        code("invalid_cursor", () -> ledger.events(scope, id, 4, 10));
        code("input_too_large", () -> ledger.append(lease, "large", "message.delta", json.createObjectNode().put("text", "x".repeat(16384))));
        jdbc.update("UPDATE oa_agent_run SET event_bytes=1048576 WHERE run_id=?", id);
        code("event_limit", () -> ledger.append(lease, "next", "message.delta", body));
        assertEquals("succeeded", ledger.finish(lease, RunState.SUCCEEDED, null).get("state"));
        assertTrue(ledger.events(scope, id, 3, 10).terminal());
    }

    @Test void eventRetentionIsExplicitEvenBeforeGarbageCollection() {
        String id = submit("a").runId(); claim(id);
        jdbc.update("UPDATE oa_run_event SET retain_until=TIMESTAMPADD(SECOND,-1,CURRENT_TIMESTAMP(3)) WHERE run_id=? AND sequence_no=1", id);
        code("event_history_expired", () -> ledger.events(scope, id, 0, 20));
        assertEquals(1, ledger.events(scope, id, 1, 20).events().size());
        assertEquals("running", state(id));
    }

    @Test void newControlPlaneInstanceReadsExistingRunAndCannotPrematurelyReleaseIt() {
        String id = submit("a").runId(); var lease = claim(id);
        assertEquals("running", instance().get(scope, id).get("state"));
        assertEquals(2, instance().events(scope, id, 0, 20).events().size());
        code("cleanup_before_terminal", () -> ledger.resourcesReleased(lease));
        assertEquals(1, jdbc.queryForObject("SELECT COUNT(*) FROM oa_run_attempt WHERE lease_state='held'", Integer.class));
    }

    @Test @EnabledIfEnvironmentVariable(named = "OA_RUN_TEST_RESTART_SIGNAL", matches = ".+")
    void databaseCrashAndRestartPreserveRunOutboxAndIdempotency() throws Exception {
        String id = submit("restart").runId();
        var signal = java.nio.file.Path.of(System.getenv("OA_RUN_TEST_RESTART_SIGNAL"));
        java.nio.file.Files.writeString(signal, "restart");
        long until = System.nanoTime() + TimeUnit.SECONDS.toNanos(60);
        while (!java.nio.file.Files.readString(signal).equals("ready") && System.nanoTime() < until) Thread.sleep(100);
        assertEquals("ready", java.nio.file.Files.readString(signal), "Isolated database restart did not complete");
        assertEquals("queued", instance().get(scope, id).get("state"));
        assertEquals(1, instance().events(scope, id, 0, 20).events().size());
        assertEquals(1, count("oa_run_outbox"));
        assertEquals(id, submit("restart").runId());
        assertTrue(ledger.claim(scope, id, "after-restart").isPresent());
    }

    @Test void outboxConcurrentPublishersDoNotClaimTheSameDelivery() throws Exception {
        for (int i = 0; i < 10; i++) submit("dispatch-" + i);
        List<List<RunOutbox.Delivery>> batches = concurrent(List.of(() -> outbox().claim(4, 60), () -> outbox().claim(4, 60), () -> outbox().claim(4, 60)));
        var deliveries = batches.stream().flatMap(List::stream).toList();
        assertEquals(10, deliveries.size()); assertEquals(10, deliveries.stream().map(RunOutbox.Delivery::id).distinct().count());
        assertTrue(outbox().claim(10, 60).isEmpty());
        for (var delivery : deliveries) assertTrue(outbox().delivered(delivery));
        assertTrue(outbox().claim(10, 60).isEmpty());
    }

    @Test void publisherCrashIsRedeliveredWithNewClaimAndStaleAckCannotWin() {
        submit("dispatch");
        var first = outbox().claim(1, 60).getFirst();
        jdbc.update("UPDATE oa_run_outbox SET claim_until=TIMESTAMPADD(SECOND,-1,CURRENT_TIMESTAMP(3)) WHERE id=?", first.id());
        assertFalse(outbox().delivered(first));
        var recovered = outbox().claim(1, 60).getFirst();
        assertEquals(first.runId(), recovered.runId()); assertNotEquals(first.claimId(), recovered.claimId());
        assertEquals(2, recovered.deliveryAttempt());
        assertFalse(outbox().retryLater(first, 5)); assertFalse(outbox().delivered(first));
        assertTrue(outbox().delivered(recovered)); assertFalse(outbox().delivered(recovered));
    }

    @Test void deliveryRetryIsBoundedAndDoesNotChangeRunState() {
        String id = submit("dispatch").runId(); var delivery = outbox().claim(1, 60).getFirst();
        assertTrue(outbox().retryLater(delivery, 60)); assertTrue(outbox().claim(1, 60).isEmpty());
        code("invalid_dispatch_delay", () -> outbox().retryLater(delivery, 301));
        jdbc.update("UPDATE oa_run_outbox SET available_at=TIMESTAMPADD(SECOND,-1,CURRENT_TIMESTAMP(3)) WHERE id=?", delivery.id());
        assertEquals(id, outbox().claim(1, 60).getFirst().runId());
        assertEquals("queued", state(id));
    }

    @Test void runtimePermissionChecksCurrentLeaseAndRevocationBeforeReadingSecrets() {
        String id=submit("runtime").runId(); var lease=claim(id);
        var reads=new java.util.concurrent.atomic.AtomicInteger();
        assertEquals(1,ledger.withPermit(lease,reads::incrementAndGet));
        jdbc.update("UPDATE oa_model_connection_version SET revoked=true WHERE tenant_id=?",A);
        code("deployment_unavailable",()->ledger.withPermit(lease,reads::incrementAndGet));
        assertEquals(1,reads.get());
        jdbc.update("UPDATE oa_model_connection_version SET revoked=false WHERE tenant_id=?",A);
        ledger.cancel(scope,id,"alice",()->{});
        code("lease_fenced",()->ledger.withPermit(lease,reads::incrementAndGet));
        assertEquals(1,reads.get());
    }

    @Test void runtimeBundleUsesDeploymentVersionAndNeverCurrentModelPointer() {
        String id=submit("runtime").runId(); var lease=claim(id);
        var models=org.mockito.Mockito.mock(com.iflytek.astron.console.hub.platform.model.ModelConnectionService.class);
        var store=org.mockito.Mockito.mock(com.iflytek.astron.console.hub.platform.skill.SkillArtifactStore.class);
        String connection=jdbc.queryForObject("SELECT model_connection_id FROM oa_agent_deployment WHERE deployment_id=?",String.class,deployment);
        jdbc.update("UPDATE oa_model_connection SET current_version=2 WHERE connection_id=?",connection);
        var model=new com.iflytek.astron.console.hub.platform.model.ModelConnectionService.WorkerModel(
                "https://model.invalid/v1","synthetic","synthetic-credential",1,true,true,5,60,120,null);
        org.mockito.Mockito.when(models.runtimeVersionForAuthorizedLease(A,"test",connection,1)).thenReturn(model);
        var runtime=new RunRuntimeService(ledger,jdbc,models,store,json);
        var bundle=runtime.bundle(lease);
        assertEquals(1,bundle.model().version());assertEquals("hello",bundle.input().get("message").asText());
        assertEquals("Synthetic test only",bundle.snapshot().get("systemPrompt").asText());
        assertFalse(bundle.toString().contains("synthetic-credential"));
        org.mockito.Mockito.verify(models).runtimeVersionForAuthorizedLease(A,"test",connection,1);
        ledger.cancel(scope,id,"alice",()->{});
        code("lease_fenced",()->runtime.bundle(lease));
        org.mockito.Mockito.verifyNoMoreInteractions(models);
    }

    @Test void artifactBrokerRejectsUnboundReleaseAndCancellationDuringObjectRead() {
        String release=UUID.randomUUID().toString(), manifest="{\"synthetic\":true}", digest=RunJson.hash(manifest);
        jdbc.update("""
                INSERT INTO oa_skill_release(tenant_id,release_id,skill_name,description,version_label,source_kind,manifest_sha256,manifest_json,
                runtime_image,object_prefix,total_bytes,file_count,created_by,retain_until)
                VALUES(?,?,'test-skill','test','v1','upload',?,?,'registry.invalid/skill@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
                'synthetic-prefix',5,1,'alice',TIMESTAMPADD(DAY,1,CURRENT_TIMESTAMP(3)))
                """,A,release,digest,manifest);
        jdbc.update("INSERT INTO oa_skill_release_environment(tenant_id,environment,release_id,granted_by) VALUES(?,'test',?,'alice')",A,release);
        jdbc.update("INSERT INTO oa_agent_deployment_skill(tenant_id,environment,deployment_id,release_id,manifest_sha256) VALUES(?,'test',?,?,?)",
                A,deployment,release,digest);
        jdbc.update("INSERT INTO oa_skill_release_file(tenant_id,release_id,file_index,file_path,path_sha256,content_sha256,size_bytes,object_key) VALUES(?,?,0,'SKILL.md',?,?,5,'synthetic-object')",
                A,release,RunJson.hash("SKILL.md"),RunJson.hash("hello"));
        String id=submit("runtime").runId();var lease=claim(id);
        var models=org.mockito.Mockito.mock(com.iflytek.astron.console.hub.platform.model.ModelConnectionService.class);
        var store=org.mockito.Mockito.mock(com.iflytek.astron.console.hub.platform.skill.SkillArtifactStore.class);
        var runtime=new RunRuntimeService(ledger,jdbc,models,store,json);
        code("resource_unavailable",()->runtime.artifact(lease,UUID.randomUUID().toString(),0));
        org.mockito.Mockito.verifyNoInteractions(store);
        assertArrayEquals(manifest.getBytes(StandardCharsets.UTF_8),runtime.artifact(lease,release,-1));
        org.mockito.Mockito.when(store.get("synthetic-object",5,RunJson.hash("hello"))).thenAnswer(call->{
            ledger.cancel(scope,id,"alice",()->{});return "hello".getBytes(StandardCharsets.UTF_8);
        });
        code("lease_fenced",()->runtime.artifact(lease,release,0));
    }
}
