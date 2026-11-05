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
                "V1.51__add_immutable_skill_releases.sql", "V1.52__add_durable_agent_runs.sql", "V1.53__add_run_sandbox_journal.sql", "V1.54__add_deployment_access.sql", "V1.55__add_run_supervision.sql", "V1.56__add_monitoring_query_indexes.sql", "V1.57__retain_task_outputs.sql", "V1.58__observe_agent_steps.sql"))
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
        org.springframework.web.context.request.RequestContextHolder.resetRequestAttributes();
        if (admin != null && database != null && database.matches("oa_run_verify_[a-f0-9]{32}")) admin.execute("DROP DATABASE " + database);
    }

    private DriverManagerDataSource dataSource(String url) {
        return new DriverManagerDataSource(url, System.getenv("OA_RUN_TEST_DB_USER"), System.getenv("OA_RUN_TEST_DB_PASSWORD"));
    }
    private RunLedger instance() { return new RunLedger(new JdbcTemplate(source), new DataSourceTransactionManager(source), json); }
    private TaskObservationService observations() {
        return new TaskObservationService(ledger,jdbc,new ObjectMapper().findAndRegisterModules(),authorization(),deployments(),new DataSourceTransactionManager(source));
    }
    @Test void observationPeriodsCountAllRowsByCallEndAcrossTaskAndCalendarBoundaries() {
        actors();String run=submit("period-source").runId();
        for(int i=0;i<64;i++)jdbc.update("""
                INSERT INTO oa_agent_run(tenant_id,environment,run_id,deployment_id,actor_id,idempotency_key,
                  request_sha256,input_json,snapshot_json,snapshot_sha256,trace_id,deadline_at,retain_until)
                SELECT tenant_id,environment,?,deployment_id,actor_id,?,request_sha256,input_json,snapshot_json,
                  snapshot_sha256,?,deadline_at,retain_until FROM oa_agent_run WHERE run_id=?
                """,UUID.randomUUID().toString(),"period-"+i,UUID.randomUUID().toString(),run);
        jdbc.update("UPDATE oa_agent_run SET state='succeeded',created_at='2026-11-01 16:00:00',started_at='2026-11-01 16:00:01',finished_at='2026-11-01 16:00:04'");
        jdbc.update("UPDATE oa_agent_run SET created_at='2026-10-01 00:00:00' WHERE run_id=?",run);
        for(int i=0;i<65;i++)jdbc.update("""
                INSERT INTO oa_run_step(tenant_id,environment,run_id,attempt_no,step_id,kind,name,started_at,ended_at,duration_ms,status,usage_source,input_tokens,output_tokens,cache_read_tokens,start_json,finish_json,start_hash,finish_hash)
                VALUES(?,'test',?,1,?,'model','fixture-model','2026-11-01 15:59:59','2026-11-01 16:00:00',1000,'succeeded','provider',10,2,5,'{}','{}',?,?)
                """,A,run,"call-"+i,RunJson.hash("start"),RunJson.hash("finish"));
        jdbc.update("UPDATE oa_run_step SET ended_at='2026-11-01 15:59:59.999' WHERE step_id='call-0'");
        jdbc.update("UPDATE oa_run_step SET usage_source='unknown',input_tokens=NULL,output_tokens=NULL,cache_read_tokens=NULL WHERE step_id='call-1'");
        jdbc.update("UPDATE oa_run_step SET ended_at=NULL,finish_json=NULL,finish_hash=NULL,duration_ms=NULL,status='running',usage_source='unknown',input_tokens=NULL,output_tokens=NULL,cache_read_tokens=NULL WHERE step_id='call-2'");
        var clock=java.time.Clock.fixed(java.time.Instant.parse("2026-11-02T02:00:00Z"),java.time.ZoneOffset.UTC);
        var stats=new com.iflytek.astron.console.hub.platform.AgentPeriodStatistics(jdbc,access(),clock);
        var result=stats.snapshot(A,"test","this_week");var tasks=(Map<?,?>)result.get("tasks");var usage=(Map<?,?>)result.get("usage");
        assertEquals(64,((Number)tasks.get("tasks")).intValue());
        assertEquals(3000,((Number)tasks.get("p95TaskMs")).intValue());
        assertEquals(63,((Number)usage.get("modelCalls")).intValue());
        assertEquals(1,((Number)usage.get("unknownModelCalls")).intValue());
        assertEquals(620,((Number)usage.get("inputTokens")).intValue());
        assertEquals(124,((Number)usage.get("outputTokens")).intValue());
        assertEquals(310,((Number)usage.get("cacheReadTokens")).intValue());
        assertEquals(1000,((Number)usage.get("p95ModelMs")).intValue());
        assertEquals(7,((List<?>)result.get("daily")).size());
        assertEquals(1,((Number)((Map<?,?>)stats.snapshot(A,"test","last_week").get("usage")).get("modelCalls")).intValue());
        assertEquals(1,((Number)((Map<?,?>)stats.snapshot(A,"test","last_month").get("tasks")).get("tasks")).intValue());
        assertEquals(0,((Number)((Map<?,?>)stats.snapshot(A,"prod","this_week").get("usage")).get("modelCalls")).intValue());
        user("alice");assertThrows(com.iflytek.astron.console.commons.tenant.TenantAccessDeniedException.class,()->stats.snapshot(B,"test","this_week"));
        user("bob");assertThrows(com.iflytek.astron.console.commons.tenant.TenantAccessDeniedException.class,()->stats.snapshot(A,"prod","this_week"));
        user("viewer");assertThrows(com.iflytek.astron.console.commons.tenant.TenantAccessDeniedException.class,()->stats.snapshot(A,"test","this_week"));
    }
    private TaskObservationService.Step observation(String id,String kind,java.time.Instant start,boolean done) {
        return new TaskObservationService.Step(id,null,kind,"contract_step",start,done?start.plusMillis(5):null,done?5L:null,done?"succeeded":"running",
                kind.equals("model")?(done?"provider":"unknown"):"not_applicable",kind.equals("model")&&done?12L:null,kind.equals("model")&&done?3L:null,
                kind.equals("model")&&done?7L:null,null,json.createObjectNode().put("input","synthetic metadata"));
    }
    @Test void observationsDeduplicateConcurrentDeliveryAndCountOnlyModelLeaves() throws Exception {
        actors();var service=observations();var lease=claim(submit("observations").runId());
        var start=java.time.Instant.now().truncatedTo(java.time.temporal.ChronoUnit.MILLIS);
        var open=observation("call-1","model",start,false);var closed=observation("call-1","model",start,true);
        concurrent(List.of(()->service.record(lease,"start",open),()->service.record(lease,"start",open)));
        concurrent(List.of(()->service.record(lease,"finish",closed),()->service.record(lease,"finish",closed)));
        service.record(lease,"start",open);
        service.record(lease,"start",observation("tool-1","tool",start,false));
        service.record(lease,"finish",observation("tool-1","tool",start,true));
        var page=service.list(caller("alice"),lease.runId(),0,1);
        assertEquals(true,page.get("hasMore"));assertEquals(1,((List<?>)page.get("items")).size());
        var next=service.list(caller("alice"),lease.runId(),((Number)page.get("next")).longValue(),1);
        assertEquals(false,next.get("hasMore"));assertEquals(1,((List<?>)next.get("items")).size());
        var totals=(Map<?,?>)page.get("totals");assertEquals(12L,((Number)totals.get("inputTokens")).longValue());
        assertEquals(3L,((Number)totals.get("outputTokens")).longValue());assertEquals(1L,((Number)totals.get("modelCalls")).longValue());
        code("observation_conflict",()->service.record(lease,"start",observation("call-1","tool",start,false)));
        assertEquals(2,jdbc.queryForObject("SELECT COUNT(*) FROM oa_run_step WHERE export_state='pending'",Integer.class));
    }
    @Test void observationsFenceNewStartsButRetainCancellationAndCurrentPermissions() {
        actors();var service=observations();var lease=claim(submit("observation-cancel").runId());
        var start=java.time.Instant.now().truncatedTo(java.time.temporal.ChronoUnit.MILLIS);
        service.record(lease,"start",observation("model-1","model",start,false));
        service.record(lease,"start",observation("interrupted","model",start,false));
        ledger.cancel(scope,lease.runId(),"alice",()->{});
        service.record(lease,"finish",observation("model-1","model",start,true));
        code("lease_fenced",()->service.record(lease,"start",observation("new-call","model",start,false)));
        var forged=new RunLedger.Lease(lease.scope(),lease.runId(),lease.attempt(),lease.leaseId(),lease.fencingToken()+1,lease.workerId());
        code("lease_fenced",()->service.record(forged,"finish",observation("model-1","model",start,true)));
        ledger.finish(lease,RunState.FAILED,"execution_failed");ledger.resourcesReleased(lease);
        var page=service.list(caller("alice"),lease.runId(),0,100);
        var items=(List<Map<String,Object>>)page.get("items");assertEquals("interrupted",items.get(1).get("status"));
        assertNull(items.get(1).get("input_tokens"));
        code("action_forbidden",()->service.list(caller("viewer"),lease.runId(),0,100));
        jdbc.update("DELETE FROM oa_tenant_member WHERE tenant_id=? AND environment='test' AND uid='alice'",A);
        code("resource_unavailable",()->service.list(caller("alice"),lease.runId(),0,100));
    }
    private static class OutputStore implements TaskOutputStore {
        final Map<String,byte[]> objects=new java.util.concurrent.ConcurrentHashMap<>();
        boolean fail;
        Runnable afterPut=()->{};
        @Override public void put(String key,java.io.InputStream input,long size,String sha) {
            if(fail)throw new RunFailure("output_storage_unavailable");
            try {var bytes=input.readAllBytes();assertEquals(size,bytes.length);assertEquals(sha,com.iflytek.astron.console.hub.platform.skill.SkillArchiveValidator.sha256(bytes));objects.put(key,bytes);afterPut.run();}
            catch(java.io.IOException invalid){throw new RuntimeException(invalid);}
        }
        @Override public org.springframework.core.io.Resource verified(String key,long size,String sha) {return new ByteArrayResource(objects.get(key));}
        @Override public void remove(String key) {objects.remove(key);}
    }
    private TaskOutputService outputs(OutputStore store) {
        return new TaskOutputService(ledger,jdbc,store,authorization(),deployments(),new DataSourceTransactionManager(source),new org.springframework.mock.env.MockEnvironment());
    }
    @Test void outputsPersistAcrossCleanupRestartRetentionAndRequireCurrentTaskAccess() throws Exception {
        actors();var store=new OutputStore();var service=outputs(store);var lease=claim(submit("outputs").runId());
        byte[] bytes="任务交付物\n".repeat(10000).getBytes(StandardCharsets.UTF_8);
        String sha=com.iflytek.astron.console.hub.platform.skill.SkillArchiveValidator.sha256(bytes);
        var descriptor=new TaskOutputService.Descriptor("/workspace/outputs/报告.csv",bytes.length,sha,"tool:write-1");
        var reserved=service.reserve(lease,descriptor);String id=(String)reserved.get("output_id");
        assertEquals(id,service.reserve(lease,descriptor).get("output_id"));
        code("output_not_available",()->service.download(caller("alice"),lease.runId(),id,false));
        service.upload(lease,id,new java.io.ByteArrayInputStream(bytes),bytes.length);
        assertEquals("available",service.reserve(lease,descriptor).get("state"));
        code("output_already_published",()->service.upload(lease,id,new java.io.ByteArrayInputStream(bytes),bytes.length));
        code("action_forbidden",()->service.list(caller("viewer"),lease.runId()));
        code("resource_unavailable",()->service.download(caller("alice"),submit("other-task").runId(),id,false));
        ledger.cancel(scope,lease.runId(),"alice",()->{});ledger.finish(lease,RunState.FAILED,"execution_failed");ledger.resourcesReleased(lease);
        code("lease_fenced",()->service.reserve(lease,descriptor));
        jdbc.update("UPDATE oa_agent_run SET retain_until=TIMESTAMPADD(DAY,-10,CURRENT_TIMESTAMP(3)) WHERE run_id=?",lease.runId());
        var restarted=outputs(store);restarted.reconcile();
        assertEquals(1,restarted.list(caller("alice"),lease.runId()).size());
        try(var input=restarted.download(caller("alice"),lease.runId(),id,false).content().getInputStream()) {assertArrayEquals(bytes,input.readAllBytes());}
        assertEquals(1,jdbc.queryForObject("SELECT COUNT(*) FROM oa_run_output_access WHERE action='download'",Integer.class));
        jdbc.update("DELETE FROM oa_tenant_member WHERE tenant_id=? AND environment='test' AND uid='alice'",A);
        code("resource_unavailable",()->restarted.download(caller("alice"),lease.runId(),id,false));
        assertEquals(1,store.objects.size());
    }
    @Test void outputsRejectUnsafePathsEnforceVersionsAndReconcileOnlyUnpublishedObjects() throws Exception {
        actors();var store=new OutputStore();var service=outputs(store);var lease=claim(submit("output-limits").runId());
        for(String path:List.of("/etc/passwd","/workspace/outputs/../secret","/workspace/outputs//bad","/workspace/outputs/a\\b","/workspace/outputs/a\nb"))
            code("invalid_output",()->service.reserve(lease,new TaskOutputService.Descriptor(path,1,RunJson.hash("x"),"final")));
        var one=service.reserve(lease,new TaskOutputService.Descriptor("/workspace/outputs/a.html",1,RunJson.hash("a"),"tool:1"));
        String id=(String)one.get("output_id");store.fail=true;
        code("output_storage_unavailable",()->service.upload(lease,id,new java.io.ByteArrayInputStream(new byte[]{'a'}),1));
        assertEquals("failed",service.list(caller("alice"),lease.runId()).getFirst().get("state"));
        store.fail=false;service.upload(lease,id,new java.io.ByteArrayInputStream(new byte[]{'a'}),1);
        code("output_preview_unavailable",()->service.download(caller("alice"),lease.runId(),id,true));
        var two=service.reserve(lease,new TaskOutputService.Descriptor("/workspace/outputs/a.html",1,RunJson.hash("b"),"tool:2"));
        assertNotEquals(id,two.get("output_id"));
        for(int i=0;i<30;i++)service.reserve(lease,new TaskOutputService.Descriptor("/workspace/outputs/f"+i,0,RunJson.hash(""),"final"));
        code("output_quota_exceeded",()->service.reserve(lease,new TaskOutputService.Descriptor("/workspace/outputs/excess",0,RunJson.hash(""),"final")));
        jdbc.update("UPDATE oa_run_output_upload SET created_at=TIMESTAMPADD(MINUTE,-20,CURRENT_TIMESTAMP(3))");
        service.reconcile();assertEquals(1,store.objects.size());
        assertEquals(1,jdbc.queryForObject("SELECT COUNT(*) FROM oa_run_output_upload WHERE state='cleared'",Integer.class));
        assertEquals(1,jdbc.queryForObject("SELECT COUNT(*) FROM oa_run_output_upload WHERE state='published'",Integer.class));
    }
    @Test void outputsConcurrentReservationAndRevocationDuringIoAreFenced() throws Exception {
        actors();var store=new OutputStore();var service=outputs(store);var lease=claim(submit("output-concurrent").runId());
        var descriptor=new TaskOutputService.Descriptor("/workspace/outputs/a.txt",1,RunJson.hash("a"),"tool:1");
        List<Map<String,Object>> rows=concurrent(List.of(()->service.reserve(lease,descriptor),()->service.reserve(lease,descriptor),()->service.reserve(lease,descriptor)));
        assertEquals(1,rows.stream().map(row->row.get("output_id")).distinct().count());
        String id=(String)rows.getFirst().get("output_id");
        store.afterPut=()->ledger.cancel(scope,lease.runId(),"alice",()->{});
        code("lease_fenced",()->service.upload(lease,id,new java.io.ByteArrayInputStream(new byte[]{'a'}),1));
        assertEquals("failed",service.list(caller("alice"),lease.runId()).getFirst().get("state"));
        code("output_not_available",()->service.download(caller("alice"),lease.runId(),id,false));
        jdbc.update("UPDATE oa_run_output_upload SET created_at=TIMESTAMPADD(MINUTE,-20,CURRENT_TIMESTAMP(3))");
        service.reconcile();assertTrue(store.objects.isEmpty());
    }
    private RunOutbox outbox() { return new RunOutbox(new JdbcTemplate(source), new DataSourceTransactionManager(source)); }
    private RunSandboxJournal journal() { return new RunSandboxJournal(instance(), new JdbcTemplate(source)); }
    private static final String SUPERVISOR = "supervisor-1", OTHER_SUPERVISOR = "supervisor-2";
    private static final String BOOT = "5d93bc29-5ae4-4fce-8f06-5d2ebd121c09", INVOCATION = "c".repeat(32);
    private RunSupervision supervision() {
        var config = new org.springframework.mock.env.MockEnvironment()
                .withProperty("PLATFORM_RECONCILER_WORKERS", "{\"supervisor-1\":\"worker-1\",\"supervisor-2\":\"worker-2\"}")
                .withProperty("PLATFORM_SERVICE_KEYS", "{\"supervisor-1\":{\"role\":\"reconciler\",\"secret\":\""+"s".repeat(32)
                        +"\"},\"supervisor-2\":{\"role\":\"reconciler\",\"secret\":\""+"t".repeat(32)
                        +"\"},\"worker-1\":{\"role\":\"worker\",\"secret\":\""+"w".repeat(32)
                        +"\"},\"worker-2\":{\"role\":\"worker\",\"secret\":\""+"v".repeat(32)+"\"}}");
        return new RunSupervision(instance(),journal(),new JdbcTemplate(source),new DataSourceTransactionManager(source),json,config);
    }
    private RunSupervision.Snapshot supervised(String key) {
        return supervision().claim(scope,submit(key).runId(),SUPERVISOR,BOOT).execution();
    }
    private RunSupervision.Proof proof(RunSupervision.Snapshot execution, String kind, String boot) {
        return new RunSupervision.Proof(UUID.randomUUID().toString(),kind,boot,execution.unitName(),execution.invocationId(),
                execution.sandboxId(),"e".repeat(64),true,true,true,true);
    }
    private static final String IMAGE = "registry.invalid/skill@sha256:" + "a".repeat(64);
    private void bindSkill(RunLedger.Scope target, String deploymentId) {
        String release = UUID.randomUUID().toString();
        jdbc.update("""
                INSERT INTO oa_skill_release(tenant_id,release_id,skill_name,description,version_label,source_kind,
                    manifest_sha256,manifest_json,runtime_image,object_prefix,total_bytes,file_count,created_by,retain_until)
                VALUES(?,?,'sandbox-test','synthetic',?,'upload',?,'{}',?,'synthetic',0,1,'alice',TIMESTAMPADD(DAY,1,CURRENT_TIMESTAMP(3)))
                """, target.tenant(), release, release, RunJson.hash("{}"), IMAGE);
        jdbc.update("INSERT INTO oa_skill_release_environment(tenant_id,environment,release_id,granted_by) VALUES(?,?,?,'alice')",
                target.tenant(), target.environment(), release);
        jdbc.update("INSERT INTO oa_agent_deployment_skill(tenant_id,environment,deployment_id,release_id,manifest_sha256) VALUES(?,?,?,?,?)",
                target.tenant(), target.environment(), deploymentId, release, RunJson.hash("{}"));
    }
    private RunLedger.Lease reserved(String key) {
        var lease = claim(submit(key).runId());
        journal().reserve(lease, "gvisor-demo", IMAGE);
        return lease;
    }
    private String sandboxPhase(RunLedger.Lease lease) {
        return jdbc.queryForObject("SELECT phase FROM oa_run_sandbox WHERE lease_id=?", String.class, lease.leaseId());
    }
    private void actors() {
        jdbc.update("INSERT INTO user_info(uid,username,account_status,deleted) VALUES('alice','Alice',1,0),('bob','Bob',1,0),('viewer','Viewer',1,0),('operator','Operator',1,0)");
        jdbc.update("INSERT INTO agent_space(id,name,uid,deleted) VALUES(10,'A','alice',0),(20,'B','bob',0)");
        jdbc.update("INSERT INTO agent_space_user(space_id,uid,role) VALUES(10,'alice',1),(10,'bob',3),(10,'viewer',3),(10,'operator',3),(20,'bob',1)");
        jdbc.update("""
                INSERT INTO oa_tenant_member(tenant_id,environment,uid,role) VALUES
                ('space-10','test','alice','tenant_admin'),('space-10','prod','alice','tenant_admin'),
                ('space-10','test','bob','developer'),('space-10','prod','bob','viewer'),
                ('space-10','test','viewer','viewer'),('space-10','test','operator','operator'),('space-10','prod','operator','operator'),
                ('space-20','test','bob','tenant_admin')
                """);
        user("alice");
    }
    private void user(String uid) {
        var request=new org.springframework.mock.web.MockHttpServletRequest();
        request.setAttribute(com.iflytek.astron.console.commons.config.JwtClaimsFilter.USER_ID_ATTRIBUTE,uid);
        org.springframework.web.context.request.RequestContextHolder.setRequestAttributes(new org.springframework.web.context.request.ServletRequestAttributes(request));
    }
    private com.iflytek.astron.console.commons.tenant.TenantAccessRepository memberships() {
        return new com.iflytek.astron.console.commons.tenant.TenantAccessRepository(jdbc);
    }
    private com.iflytek.astron.console.commons.tenant.TenantAccessService access() {
        return new com.iflytek.astron.console.commons.tenant.TenantAccessService(memberships());
    }
    private AgentDeploymentService deployments() { return new AgentDeploymentService(jdbc,access(),json,new DataSourceTransactionManager(source)); }
    private DeploymentKeys keys() { return new DeploymentKeys(jdbc,access(),deployments(),new DataSourceTransactionManager(source)); }
    private DeploymentAuthorization authorization() { return new DeploymentAuthorization(jdbc,memberships()); }

    @Test void monitoringCountsFullWindowAndRetainsUnknownUsageAndUnreleasedSlots() {
        actors();
        String original=submit("monitor-source").runId();
        for (int i=0;i<64;i++) jdbc.update("""
                INSERT INTO oa_agent_run(tenant_id,environment,run_id,deployment_id,actor_id,idempotency_key,
                  request_sha256,input_json,snapshot_json,snapshot_sha256,trace_id,deadline_at,retain_until)
                SELECT tenant_id,environment,?,deployment_id,actor_id,?,request_sha256,input_json,snapshot_json,
                  snapshot_sha256,?,deadline_at,retain_until FROM oa_agent_run WHERE run_id=?
                """,UUID.randomUUID().toString(),"monitor-"+i,UUID.randomUUID().toString(),original);
        jdbc.update("UPDATE oa_agent_run SET state='succeeded',created_at=TIMESTAMPADD(MINUTE,-3,CURRENT_TIMESTAMP(3)),started_at=TIMESTAMPADD(SECOND,-150,CURRENT_TIMESTAMP(3)),finished_at=TIMESTAMPADD(SECOND,-90,CURRENT_TIMESTAMP(3))");
        jdbc.update("UPDATE oa_agent_run SET usage_json='{\"prompt_tokens\":6,\"completion_tokens\":4,\"total_tokens\":10}' ORDER BY run_id LIMIT 20");
        bindSkill(scope,deployment);
        var held=reserved("monitor-running");
        String old=submit("monitor-outside-window").runId();
        jdbc.update("UPDATE oa_agent_run SET state='cancelled',created_at=TIMESTAMPADD(DAY,-8,CURRENT_TIMESTAMP(3)),finished_at=TIMESTAMPADD(DAY,-8,CURRENT_TIMESTAMP(3)) WHERE run_id=?",old);
        var other=new RunLedger.Scope(B,"test"); var prod=new RunLedger.Scope(A,"prod");
        ledger.submit(other,seedDeployment(other),"bob","other",json.createObjectNode().put("message","private"),3600,()->{});
        ledger.submit(prod,seedDeployment(prod),"alice","prod",json.createObjectNode().put("message","private"),3600,()->{});
        var statistics=new com.iflytek.astron.console.hub.platform.PlatformStatisticsService(jdbc,access());
        var snapshot=statistics.snapshot(A,"test","24h");
        var totals=(Map<?,?>)snapshot.get("totals");var current=(Map<?,?>)snapshot.get("current");
        assertEquals(66,((Number)totals.get("total")).intValue());
        assertEquals(20,((Number)totals.get("reportedUsageRuns")).intValue());
        assertEquals(200,((Number)totals.get("totalTokens")).intValue());
        assertEquals(60,((Number)totals.get("meanRunSeconds")).doubleValue(),0.01);
        assertEquals(65,((Number)totals.get("timedRuns")).intValue());
        assertEquals(1,((Number)current.get("occupiedExecutionSlots")).intValue());
        assertEquals(1,((Number)current.get("unreleasedSandboxes")).intValue());
        assertEquals(1,((Number)((Map<?,?>)statistics.snapshot(A,"prod","7d").get("totals")).get("total")).intValue());
        user("bob");assertEquals(1,((Number)((Map<?,?>)statistics.snapshot(B,"test","1h").get("totals")).get("total")).intValue());
        user("alice");assertThrows(com.iflytek.astron.console.commons.tenant.TenantAccessDeniedException.class,()->statistics.snapshot(B,"test","24h"));
        user("viewer");assertThrows(com.iflytek.astron.console.commons.tenant.TenantAccessDeniedException.class,()->statistics.snapshot(A,"test","24h"));
        user("bob");assertThrows(com.iflytek.astron.console.commons.tenant.TenantAccessDeniedException.class,()->statistics.snapshot(A,"prod","24h"));
        try(var metrics=new com.iflytek.astron.platform.OperationsMetrics(jdbc)) {
            metrics.collect();String scrape=metrics.scrape();
            assertTrue(scrape.contains("open_agent_metrics_collection_success 1.0"));
            assertFalse(scrape.contains(held.leaseId())); assertFalse(scrape.contains("space-10")); assertFalse(scrape.contains("private"));
        }
    }

    @Test void monitoringEmptyWindowIsNotZeroTokenUsageAndRevocationIsRechecked() {
        actors(); var statistics=new com.iflytek.astron.console.hub.platform.PlatformStatisticsService(jdbc,access());
        var totals=(Map<?,?>)statistics.snapshot(A,"test","1h").get("totals");
        assertEquals(0,((Number)totals.get("total")).intValue());assertNull(totals.get("totalTokens"));assertNull(totals.get("meanRunSeconds"));
        assertThrows(IllegalArgumentException.class,()->statistics.snapshot(A,"test","all"));
        jdbc.update("DELETE FROM oa_tenant_member WHERE tenant_id=? AND environment='test' AND uid='alice'",A);
        assertThrows(com.iflytek.astron.console.commons.tenant.TenantAccessDeniedException.class,()->statistics.snapshot(A,"test","1h"));
    }

    @Test void profileRegistrationIsConcurrentIdempotentAndGrantsNothing() throws Exception {
        var profiles = new com.iflytek.astron.console.hub.platform.CurrentProfileService(jdbc);
        try (var pool = Executors.newFixedThreadPool(4)) {
            var gate = new CountDownLatch(1);
            var futures = new ArrayList<java.util.concurrent.Future<Map<String,Object>>>();
            for (int i=0;i<4;i++) futures.add(pool.submit(() -> {
                gate.await(); return profiles.register("new-subject", "New User");
            }));
            gate.countDown();
            for (var future:futures) assertEquals("new-subject",future.get(15,TimeUnit.SECONDS).get("uid"));
        }
        assertEquals(1,jdbc.queryForObject("SELECT COUNT(*) FROM user_info WHERE uid='new-subject'",Integer.class));
        assertEquals("New User",profiles.register("new-subject","Changed name").get("displayName"));
        for (String table:List.of("oa_tenant_member","oa_platform_admin","agent_space_user")) assertEquals(0,count(table));
        assertEquals(2,count("oa_tenant"));
        assertEquals(0,jdbc.queryForObject("SELECT user_agreement FROM user_info WHERE uid='new-subject'",Integer.class));
        user("new-subject");
        var governance = new com.iflytek.astron.console.hub.platform.TenantGovernanceService(jdbc,access(),json);
        assertFalse(governance.isPlatformAdmin()); assertTrue(governance.tenants().isEmpty());
    }

    @Test void profileRegistrationDoesNotReactivateOrMergeCollidingSubjects() {
        var profiles = new com.iflytek.astron.console.hub.platform.CurrentProfileService(jdbc);
        jdbc.update("INSERT INTO user_info(uid,username,account_status,deleted) VALUES('frozen','Keep',2,0),('deleted','Keep',1,1),('Alice','Keep',1,0)");
        for (String subject:List.of("frozen","deleted","alice","Álice"))
            assertThrows(com.iflytek.astron.console.hub.platform.CurrentProfileService.ProfileUnavailable.class,
                    ()->profiles.register(subject,"Overwrite"));
        assertEquals(3,count("user_info"));
        assertEquals(2,jdbc.queryForObject("SELECT account_status FROM user_info WHERE uid='frozen'",Integer.class));
        assertEquals(1,jdbc.queryForObject("SELECT deleted FROM user_info WHERE uid='deleted'",Integer.class));
        assertEquals("Keep",profiles.register("Alice","Overwrite").get("displayName"));
        assertEquals(0,count("oa_tenant_member"));
    }
    private AgentRunService api(DeploymentAuthorization authorization) {
        return new AgentRunService(ledger,authorization,deployments(),jdbc,json,new org.springframework.mock.env.MockEnvironment()
                .withProperty("PLATFORM_RUN_ADMISSION_ENABLED","true"),new DataSourceTransactionManager(source));
    }
    private AgentRunService api() { return api(authorization()); }
    private AgentRunService.Submit input() { return new AgentRunService.Submit(new AgentRunService.Input("synthetic user input",List.of()),300); }
    private DeploymentAuthorization.Caller caller(String uid) { return new DeploymentAuthorization.Caller(uid,null); }
    private DeploymentAuthorization.Caller caller(DeploymentKeys.Issued issued) {
        return new DeploymentAuthorization.Caller(null,keys().authenticate(issued.token()).orElseThrow());
    }
    private AgentDeploymentService.Create configuration() {
        String model=jdbc.queryForObject("SELECT model_connection_id FROM oa_agent_deployment WHERE deployment_id=?",String.class,deployment);
        return new AgentDeploymentService.Create("Demo agent","v1",UUID.fromString(model),1,List.of(),new AgentDeploymentService.Execution("Synthetic instructions",null,null,null,null));
    }

    @Test void apiDeploymentCreationPinsModelVersionAndReleaseReferences() {
        actors();bindSkill(scope,deployment);
        String release=jdbc.queryForObject("SELECT release_id FROM oa_agent_deployment_skill WHERE deployment_id=?",String.class,deployment);
        var base=configuration();
        var config=new AgentDeploymentService.Create(base.displayName(),base.agentVersion(),base.modelConnectionId(),1,List.of(UUID.fromString(release)),base.execution());
        var result=deployments().create(scope,config);String id=(String)result.get("deployment_id");
        assertEquals("test",result.get("state"));assertFalse(result.containsKey("snapshot_json"));
        assertEquals(1,jdbc.queryForObject("SELECT COUNT(*) FROM oa_skill_release_reference WHERE reference_type='deployment' AND reference_id=? AND retain_until IS NULL",Integer.class,id));
        String snapshot=jdbc.queryForObject("SELECT snapshot_json FROM oa_agent_deployment WHERE deployment_id=?",String.class,id);
        assertEquals(result.get("snapshot_sha256"),RunJson.hash(snapshot));
        assertTrue(snapshot.contains("deepagents"));assertTrue(snapshot.contains(release));
        jdbc.update("UPDATE oa_model_connection SET current_version=999 WHERE connection_id=?",base.modelConnectionId().toString());
        assertEquals(1L,((Number)authorization().resolve(id).metadata().get("model_version")).longValue());
        user("viewer");
        assertThrows(com.iflytek.astron.console.commons.tenant.TenantAccessDeniedException.class,()->deployments().create(scope,base));
    }

    @Test void apiKeysAreScopedToDeploymentAndOwnRunsAndRotationPreservesSubject() {
        actors();var issued=keys().issue(scope,deployment,new DeploymentKeys.KeyInput("integration",true,true,true,30));
        assertFalse(issued.toString().contains(issued.token()));
        String stored=jdbc.queryForObject("SELECT secret_sha256 FROM oa_deployment_credential WHERE key_id=?",String.class,issued.keyId());
        assertEquals(RunJson.hash(issued.token()),stored);assertFalse(keys().list(scope,deployment).toString().contains(stored));
        var firstCaller=caller(issued);var accepted=api().submit(firstCaller,deployment,"same",input());
        assertEquals(accepted.run_id(),api().submit(firstCaller,deployment,"same",input()).run_id());
        var other=keys().issue(scope,deployment,new DeploymentKeys.KeyInput("other",true,true,true,30));
        code("resource_unavailable",()->api().status(caller(other),accepted.run_id()));
        String another=seedDeployment(scope);
        code("resource_unavailable",()->api().submit(firstCaller,another,"wrong-deployment",input()));
        var rotated=keys().rotate(scope,deployment,issued.keyId());
        assertTrue(keys().authenticate(issued.token()).isEmpty());assertEquals(firstCaller.actor(),caller(rotated).actor());
        code("resource_unavailable",()->api().status(firstCaller,accepted.run_id()));
        assertEquals("queued",api().status(caller(rotated),accepted.run_id()).get("state"));
        assertEquals(1,api().list(caller(rotated),deployment).size());
        assertEquals("cancelled",api().cancel(caller(rotated),accepted.run_id()).get("state"));
        var authenticatedBeforeRevocation=caller(rotated);keys().revoke(scope,deployment,rotated.keyId());
        code("resource_unavailable",()->api().events(authenticatedBeforeRevocation,accepted.run_id(),0));
        assertEquals(1,count("oa_agent_run"));
    }

    @Test void apiKeyActionsExpiryAndTenantSuspensionAreRechecked() {
        actors();var key=keys().issue(scope,deployment,new DeploymentKeys.KeyInput("create only",true,false,false,1));
        var caller=caller(key);String run=api().submit(caller,deployment,"create",input()).run_id();
        assertEquals(Map.of("create",true,"read",false,"cancel",false),api().deployment(caller,deployment).get("allowed_actions"));
        code("action_forbidden",()->api().status(caller,run));code("action_forbidden",()->api().cancel(caller,run));
        jdbc.update("UPDATE oa_deployment_credential SET expires_at=TIMESTAMPADD(SECOND,-1,CURRENT_TIMESTAMP(3)) WHERE key_id=?",key.keyId());
        assertTrue(keys().authenticate(key.token()).isEmpty());code("resource_unavailable",()->api().submit(caller,deployment,"expired",input()));
        code("credential_expired",()->keys().rotate(scope,deployment,key.keyId()));
        var active=keys().issue(scope,deployment,new DeploymentKeys.KeyInput("active",true,true,true,1));var activeCaller=caller(active);
        jdbc.update("UPDATE oa_tenant SET status='suspended' WHERE tenant_id=?",A);
        code("resource_unavailable",()->api().submit(activeCaller,deployment,"suspended",input()));
        assertEquals("cancelled",api().cancel(caller("alice"),run).get("state"));
    }

    @Test void apiUserRoleAndEnvironmentCannotBeForgedAndRevocationHidesOldRuns() {
        actors();var alice=caller("alice");String run=api().submit(alice,deployment,"alice",input()).run_id();
        code("action_forbidden",()->api().submit(caller("viewer"),deployment,"viewer",input()));
        code("action_forbidden",()->api().status(caller("viewer"),run));
        String other=seedDeployment(new RunLedger.Scope(B,"test"));
        code("resource_unavailable",()->api().submit(alice,other,"foreign",input()));
        jdbc.update("DELETE FROM oa_tenant_member WHERE tenant_id=? AND environment='test' AND uid='alice'",A);
        code("resource_unavailable",()->api().submit(alice,deployment,"alice",input()));
        code("resource_unavailable",()->api().events(alice,run,0));
    }

    @Test void apiRevocationBetweenInitialAuthorizationAndAdmissionRollsBackSubmission() {
        actors();var key=keys().issue(scope,deployment,new DeploymentKeys.KeyInput("race",true,true,true,1));var caller=caller(key);
        var checked=org.mockito.Mockito.spy(authorization());var calls=new java.util.concurrent.atomic.AtomicInteger();
        org.mockito.Mockito.doAnswer(invocation->{
            invocation.callRealMethod();if(calls.incrementAndGet()==1) keys().revoke(scope,deployment,key.keyId());return null;
        }).when(checked).require(org.mockito.ArgumentMatchers.any(),org.mockito.ArgumentMatchers.any(),org.mockito.ArgumentMatchers.eq(DeploymentAuthorization.Action.CREATE),org.mockito.ArgumentMatchers.isNull());
        code("resource_unavailable",()->api(checked).submit(caller,deployment,"race",input()));
        assertEquals(0,count("oa_agent_run"));assertEquals(0,count("oa_run_outbox"));
    }

    @Test void apiProductionPromotionNeedsSuccessfulExactTestAndExplicitExecutionGrant() {
        actors();var created=deployments().create(scope,configuration());String testId=(String)created.get("deployment_id");
        String prodTemplate=seedDeployment(new RunLedger.Scope(A,"prod"));
        String prodModel=jdbc.queryForObject("SELECT model_connection_id FROM oa_agent_deployment WHERE deployment_id=?",String.class,prodTemplate);
        String run=api().submit(caller("alice"),testId,"verification",input()).run_id();
        var promotion=new AgentDeploymentService.Promote(UUID.fromString(testId),UUID.fromString(prodModel),1,UUID.fromString(run));
        assertThrows(com.iflytek.astron.console.commons.tenant.TenantAccessDeniedException.class,()->deployments().promote(A,promotion));
        user("operator");code("successful_test_required",()->deployments().promote(A,promotion));
        var execution=supervision().claim(scope,run,SUPERVISOR,BOOT).execution();
        supervision().begin(execution.lease().leaseId(),SUPERVISOR);
        execution=supervision().observed(execution.lease().leaseId(),SUPERVISOR,INVOCATION);
        ledger.finish(execution.lease(),RunState.SUCCEEDED,null);
        code("successful_test_required",()->deployments().promote(A,promotion));
        supervision().release(execution.lease().leaseId(),SUPERVISOR,proof(execution,"stopped_unit",BOOT));
        String prod=(String)deployments().promote(A,promotion).get("deployment_id");
        assertEquals(1,count("oa_deployment_promotion"));
        code("action_forbidden",()->api().submit(caller("operator"),prod,"role-is-not-execution",input()));
        code("action_forbidden",()->api().submit(caller("bob"),prod,"before-grant",input()));
        user("alice");
        var controller=new PlatformDeploymentController(deployments(),keys(),access(),memberships(),jdbc,new DataSourceTransactionManager(source));
        controller.grant(A,"prod",UUID.fromString(prod),"bob");
        String prodRun=api().submit(caller("bob"),prod,"granted",input()).run_id();
        assertEquals("queued",api().status(caller("bob"),prodRun).get("state"));
        controller.revokeGrant(A,"prod",UUID.fromString(prod),"bob");
        code("action_forbidden",()->api().status(caller("bob"),prodRun));
        assertEquals("queued",api().status(caller("operator"),prodRun).get("state"));
    }

    @Test void apiDisabledDeploymentRejectsNewRunsButPreservesAuthorizedHistory() {
        actors();String run=api().submit(caller("alice"),deployment,"first",input()).run_id();
        deployments().disable(scope,deployment);
        code("deployment_unavailable",()->api().submit(caller("alice"),deployment,"disabled",input()));
        assertEquals("queued",api().status(caller("alice"),run).get("state"));
        assertEquals("cancelled",api().cancel(caller("alice"),run).get("state"));
    }

    private String skillDeployment() {
        bindSkill(scope,deployment);
        String release=jdbc.queryForObject("SELECT release_id FROM oa_agent_deployment_skill WHERE deployment_id=?",String.class,deployment);
        var base=configuration();
        return (String)deployments().create(scope,new AgentDeploymentService.Create(base.displayName(),base.agentVersion(),base.modelConnectionId(),1,
                List.of(UUID.fromString(release)),base.execution())).get("deployment_id");
    }
    private RunSupervision.Snapshot finishedSkill(String testId,String key,RunState state,boolean created) {
        String run=api().submit(caller("alice"),testId,key,input()).run_id();
        var execution=supervision().claim(scope,run,SUPERVISOR,BOOT).execution();var lease=execution.lease();
        supervision().begin(lease.leaseId(),SUPERVISOR);
        supervision().observed(lease.leaseId(),SUPERVISOR,INVOCATION);
        journal().reserve(lease,"gvisor-demo",IMAGE);
        if(created) {journal().creating(lease);journal().created(lease,UUID.randomUUID().toString());}
        if(state==RunState.CANCELLED) ledger.cancel(scope,run,"alice",()->{});
        ledger.finish(lease,state==RunState.CANCELLED?RunState.SUCCEEDED:state,null);
        return supervision().inspect(lease.leaseId(),SUPERVISOR);
    }
    private void releaseSkill(RunSupervision.Snapshot execution) {
        var evidence=proof(execution,"stopped_unit",BOOT);
        supervision().release(execution.lease().leaseId(),SUPERVISOR,evidence);
        supervision().release(execution.lease().leaseId(),SUPERVISOR,evidence);
    }

    @Test void skillEvidenceRequiresSuccessfulExactSnapshotAndCreatedSandboxAfterIndependentRelease() {
        actors();String testId=skillDeployment();
        for(var state:List.of(RunState.FAILED,RunState.CANCELLED)) {
            releaseSkill(finishedSkill(testId,state.name(),state,true));
            assertEquals(0,count("oa_skill_verification"));
        }
        releaseSkill(finishedSkill(testId,"no-created-resource",RunState.SUCCEEDED,false));
        assertEquals(0,count("oa_skill_verification"));
        var mismatched=finishedSkill(testId,"wrong-snapshot",RunState.SUCCEEDED,true);
        jdbc.update("UPDATE oa_agent_run SET snapshot_json=JSON_SET(snapshot_json,'$.skills[0].manifestSha256',?) WHERE run_id=?",
                "f".repeat(64),mismatched.lease().runId());
        releaseSkill(mismatched);assertEquals(0,count("oa_skill_verification"));
        var success=finishedSkill(testId,"success",RunState.SUCCEEDED,true);
        assertEquals(0,count("oa_skill_verification"));
        releaseSkill(success);
        assertEquals(1,count("oa_skill_verification"));
        assertEquals(success.lease().runId(),jdbc.queryForObject("SELECT run_id FROM oa_skill_verification",String.class));
        assertEquals(RunJson.hash("{}"),jdbc.queryForObject("SELECT manifest_sha256 FROM oa_skill_verification",String.class));
    }

    @Test void skillPromotionIsAtomicAndRequiresEvidenceFromTheSelectedRun() {
        actors();String testId=skillDeployment();
        var success=finishedSkill(testId,"success",RunState.SUCCEEDED,true);releaseSkill(success);
        String prodTemplate=seedDeployment(new RunLedger.Scope(A,"prod"));
        String prodModel=jdbc.queryForObject("SELECT model_connection_id FROM oa_agent_deployment WHERE deployment_id=?",String.class,prodTemplate);
        var promotion=new AgentDeploymentService.Promote(UUID.fromString(testId),UUID.fromString(prodModel),1,UUID.fromString(success.lease().runId()));
        user("operator");
        var badModel=new AgentDeploymentService.Promote(promotion.sourceDeploymentId(),UUID.randomUUID(),1,promotion.verificationRunId());
        code("model_unavailable",()->deployments().promote(A,badModel));
        assertEquals(0,jdbc.queryForObject("SELECT COUNT(*) FROM oa_skill_release_environment WHERE environment='prod'",Integer.class));
        assertEquals(0,count("oa_deployment_promotion"));
        jdbc.update("UPDATE oa_skill_verification SET passed=false");
        code("skill_verification_required",()->deployments().promote(A,promotion));
        jdbc.update("UPDATE oa_skill_verification SET passed=true,manifest_sha256=?","f".repeat(64));
        code("skill_verification_required",()->deployments().promote(A,promotion));
        jdbc.update("UPDATE oa_skill_verification SET manifest_sha256=?,run_id=?",RunJson.hash("{}"),UUID.randomUUID().toString());
        code("skill_verification_required",()->deployments().promote(A,promotion));
        jdbc.update("UPDATE oa_skill_verification SET run_id=?",success.lease().runId());
        String prod=(String)deployments().promote(A,promotion).get("deployment_id");
        assertEquals(1,count("oa_deployment_promotion"));
        assertEquals(1,jdbc.queryForObject("SELECT COUNT(*) FROM oa_skill_release_environment WHERE environment='prod'",Integer.class));
        assertEquals(RunJson.hash("{}"),jdbc.queryForObject("SELECT manifest_sha256 FROM oa_agent_deployment_skill WHERE deployment_id=?",String.class,prod));
    }

    @Test void apiUsageIsDurableAndDuplicateOldEventsCannotRegressIt() throws Exception {
        actors();String run=api().submit(caller("alice"),deployment,"usage",input()).run_id();var lease=claim(run);
        assertNull(api().list(caller("alice"),deployment).getFirst().get("usage"));
        var first=json.readTree("{\"prompt_tokens\":10,\"completion_tokens\":2,\"total_tokens\":12}");
        var last=json.readTree("{\"prompt_tokens\":20,\"completion_tokens\":5,\"total_tokens\":25}");
        ledger.append(lease,"usage-1","usage",first);ledger.append(lease,"usage-2","usage",last);ledger.append(lease,"usage-1","usage",first);
        assertEquals(25,((com.fasterxml.jackson.databind.JsonNode)api().status(caller("alice"),run).get("usage")).get("total_tokens").intValue());
        var listed = api().list(caller("alice"),deployment).getFirst();
        assertEquals(25,((com.fasterxml.jackson.databind.JsonNode)listed.get("usage")).get("total_tokens").intValue());
        assertFalse(listed.containsKey("usage_json"));
        long before=((Number)ledger.get(scope,run).get("last_event_seq")).longValue();
        code("invalid_usage",()->ledger.append(lease,"bad-usage","usage",json.readTree("{\"prompt_tokens\":-1,\"completion_tokens\":0,\"total_tokens\":0}")));
        assertEquals(before,((Number)ledger.get(scope,run).get("last_event_seq")).longValue());
        assertEquals(2,api().events(caller("alice"),run,2).events().size());
        assertFalse(api().status(caller("alice"),run).containsKey("input_json"));
    }

    @Test void apiRevokedModelCannotCommitSuccessOrAuthorizePublication() {
        actors();String run=api().submit(caller("alice"),deployment,"late-revocation",input()).run_id();
        var lease=claim(run);
        jdbc.update("UPDATE oa_model_connection_version SET revoked=true WHERE tenant_id=? AND environment='test'",A);
        var completed=ledger.finish(lease,RunState.SUCCEEDED,null);
        assertEquals("failed",completed.get("state"));assertEquals("permission_revoked",completed.get("error_code"));
    }
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

    @Test void sandboxQuotaIsAtomicAcrossInstancesAndIndependentOfRunQuota() throws Exception {
        bindSkill(scope, deployment);
        jdbc.update("UPDATE oa_tenant_environment SET max_concurrent_runs=10,max_sandboxes=3 WHERE tenant_id=? AND environment='test'", A);
        List<Callable<String>> operations = new ArrayList<>();
        for (int i=0; i<10; i++) {
            var lease = claim(submit("sandbox-"+i).runId());
            operations.add(() -> { try { journal().reserve(lease,"gvisor-demo",IMAGE); return "reserved"; }
                catch (RunFailure failure) { return failure.code(); } });
        }
        var results = concurrent(operations);
        assertEquals(3, results.stream().filter("reserved"::equals).count());
        assertEquals(7, results.stream().filter("sandbox_quota_full"::equals).count());
        assertEquals(10, count("oa_run_attempt"));
        assertEquals(3, count("oa_run_sandbox"));
    }

    @Test void sandboxCreationIsOneShotEvenAcrossLostResponseAndNewInstance() throws Exception {
        bindSkill(scope, deployment);
        var lease = reserved("one-shot");
        journal().reserve(lease,"gvisor-demo",IMAGE); // Safe before creation intent.
        var results = concurrent(List.of(
                () -> { try { journal().creating(lease); return "creating"; } catch (RunFailure e) { return e.code(); } },
                () -> { try { journal().creating(lease); return "creating"; } catch (RunFailure e) { return e.code(); } }));
        assertEquals(1, results.stream().filter("creating"::equals).count());
        assertEquals(1, results.stream().filter("sandbox_state_conflict"::equals).count());
        code("sandbox_state_conflict", () -> journal().reserve(lease,"gvisor-demo",IMAGE));
        assertEquals(1, count("oa_run_sandbox"));
    }

    @Test void supervisedClaimRollsBackAdmissionIfBindingCannotBeStored() {
        String run=submit("bind-rollback").runId();
        jdbc.execute("CREATE TRIGGER fail_supervision BEFORE INSERT ON oa_run_supervision FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='synthetic failure'");
        assertThrows(org.springframework.dao.DataAccessException.class,()->supervision().claim(scope,run,SUPERVISOR,BOOT));
        assertEquals("queued",state(run)); assertEquals(0,count("oa_run_attempt")); assertEquals(0,count("oa_run_supervision"));
        assertEquals(1,count("oa_run_event"));
        jdbc.execute("DROP TRIGGER fail_supervision");
        assertNotNull(supervision().claim(scope,run,SUPERVISOR,BOOT).lease());
    }

    @Test void supervisorLookupFindsReleasedBindingAndRejectsForeignOrUnsupervisedAttempts() {
        String run=submit("lookup").runId();
        assertNull(supervision().lookup(scope,run,SUPERVISOR).execution());
        var execution=supervision().claim(scope,run,SUPERVISOR,BOOT).execution();
        String lease=execution.lease().leaseId();
        code("resource_unavailable",()->supervision().lookup(scope,run,OTHER_SUPERVISOR));
        code("resource_unavailable",()->supervision().lookup(new RunLedger.Scope(B,"test"),run,SUPERVISOR));
        supervision().fence(lease,SUPERVISOR);
        supervision().release(lease,SUPERVISOR,proof(execution,"never_launched",BOOT));
        assertTrue(supervision().pending(SUPERVISOR,null,100).isEmpty());
        var restored=supervision().lookup(scope,run,SUPERVISOR);
        assertEquals("failed",restored.state());assertEquals("released",restored.execution().phase());
        String other=submit("unsupervised").runId();claim(other);
        code("resource_unavailable",()->supervision().lookup(scope,other,SUPERVISOR));
    }

    @Test void supervisorAbandonOnlyFinishesUnstartedQueuedRunAndHonorsDeadlineAndCancellation() {
        String run=submit("abandon").runId();
        assertEquals("failed",supervision().abandon(scope,run,SUPERVISOR).state());
        assertEquals("failed",supervision().abandon(scope,run,SUPERVISOR).state());
        assertEquals("outcome_unknown",ledger.get(scope,run).get("error_code"));
        assertEquals(0,count("oa_run_attempt"));
        assertEquals(1,jdbc.queryForObject("SELECT COUNT(*) FROM oa_tenant_audit WHERE action='run.unstarted.failed'",Integer.class));
        String expired=submit("expired").runId();
        jdbc.update("UPDATE oa_agent_run SET deadline_at=TIMESTAMPADD(SECOND,-1,CURRENT_TIMESTAMP(3)) WHERE run_id=?",expired);
        assertEquals("timed_out",supervision().abandon(scope,expired,SUPERVISOR).state());
        String cancelled=submit("cancelled").runId();ledger.cancel(scope,cancelled,"alice",()->{});
        assertEquals("cancelled",supervision().abandon(scope,cancelled,SUPERVISOR).state());
        var active=supervised("active");
        assertEquals("running",supervision().abandon(scope,active.lease().runId(),SUPERVISOR).state());
        assertTrue(ledger.heartbeat(active.lease()));
    }

    @Test void supervisorAbandonRacingClaimCannotOverwriteStartedAttempt() throws Exception {
        String run=submit("abandon-race").runId();
        concurrent(List.of(()->supervision().abandon(scope,run,SUPERVISOR),()->supervision().claim(scope,run,SUPERVISOR,BOOT)));
        var result=supervision().lookup(scope,run,SUPERVISOR);
        if (result.execution()==null) {
            assertEquals("failed",result.state());assertEquals(0,count("oa_run_attempt"));
        } else {
            assertEquals("running",result.state());assertEquals("prepared",result.execution().phase());
            assertEquals(1,count("oa_run_attempt"));assertTrue(ledger.heartbeat(result.execution().lease()));
        }
    }

    @Test void supervisedConcurrentClaimsAndLaunchPermissionProduceOnlyOneExecutor() throws Exception {
        String run=submit("bound-race").runId();
        List<RunSupervision.Claim> claims=concurrent(List.of(()->supervision().claim(scope,run,SUPERVISOR,BOOT),
                ()->supervision().claim(scope,run,OTHER_SUPERVISOR,BOOT)));
        assertEquals(1,claims.stream().filter(c->c.lease()!=null).count());
        var execution=claims.stream().filter(c->c.lease()!=null).findFirst().orElseThrow().execution();
        var lease=execution.lease(); String owner=execution.supervisorId();
        assertEquals("oa-exec-"+lease.leaseId().replace("-","")+".service",execution.unitName());
        Callable<String> begin=()->{try {return supervision().begin(lease.leaseId(),owner).phase();} catch(RunFailure e) {return e.code();}};
        var starts=concurrent(List.of(begin,begin));
        assertEquals(1,starts.stream().filter("launching"::equals).count());
        assertEquals(1,starts.stream().filter("supervision_conflict"::equals).count());
        assertEquals(1,count("oa_run_attempt"));assertEquals(1,count("oa_run_supervision"));
    }

    @Test void supervisedPreparedCancellationReleasesIdempotentlyAndRejectsChangedProof() {
        var execution=supervised("prepared-cancel");String id=execution.lease().leaseId();
        ledger.cancel(scope,execution.lease().runId(),"alice",()->{});
        code("lease_fenced",()->supervision().begin(id,SUPERVISOR));
        code("cleanup_before_terminal",()->supervision().release(id,SUPERVISOR,proof(execution,"never_launched",BOOT)));
        assertEquals("cancelled",supervision().fence(id,SUPERVISOR).state());
        var proof=proof(execution,"never_launched",BOOT);
        var result=supervision().release(id,SUPERVISOR,proof);
        assertEquals("released",result.phase());assertEquals("released",result.leaseState());assertEquals("cancelled",result.state());
        assertEquals(result,supervision().release(id,SUPERVISOR,proof));
        code("cleanup_proof_conflict",()->supervision().release(id,SUPERVISOR,proof(execution,"never_launched",BOOT)));
        assertEquals(1,jdbc.queryForObject("SELECT COUNT(*) FROM oa_tenant_audit WHERE action='execution.release.verified'",Integer.class));
    }

    @Test void supervisedUnknownLaunchKeepsQuotaUntilExactUnitInvocationIsObserved() {
        var execution=supervised("unknown-start");String id=execution.lease().leaseId();
        supervision().begin(id,SUPERVISOR);supervision().fence(id,SUPERVISOR);
        code("cleanup_unverified",()->supervision().release(id,SUPERVISOR,proof(execution,"never_launched",BOOT)));
        code("cleanup_unverified",()->supervision().release(id,SUPERVISOR,proof(execution,"stopped_unit",BOOT)));
        assertEquals("cleanup_pending",supervision().inspect(id,SUPERVISOR).leaseState());
        var observed=supervision().observed(id,SUPERVISOR,INVOCATION);
        assertEquals("failed",observed.state());
        assertEquals(observed,supervision().observed(id,SUPERVISOR,INVOCATION));
        code("supervision_conflict",()->supervision().observed(id,SUPERVISOR,"d".repeat(32)));
        assertEquals("released",supervision().release(id,SUPERVISOR,proof(observed,"stopped_unit",BOOT)).leaseState());
    }

    @Test void supervisedUnknownSandboxCreationRejectsEmptyProofUntilResourceIsIdentified() {
        bindSkill(scope,deployment);
        var execution=supervised("unknown-sandbox");var lease=execution.lease();String id=lease.leaseId();
        supervision().begin(id,SUPERVISOR);supervision().observed(id,SUPERVISOR,INVOCATION);
        journal().reserve(lease,"gvisor-demo",IMAGE);journal().creating(lease);
        ledger.cancel(scope,lease.runId(),"alice",()->{});supervision().fence(id,SUPERVISOR);
        var unknown=supervision().inspect(id,SUPERVISOR);
        code("cleanup_unverified",()->supervision().release(id,SUPERVISOR,proof(unknown,"stopped_unit",BOOT)));
        code("cleanup_unverified",()->supervision().release(id,SUPERVISOR,proof(unknown,"host_reboot",UUID.randomUUID().toString())));
        assertEquals("creating",sandboxPhase(lease)); assertEquals("cleanup_pending",unknown.leaseState());
        String sandbox=UUID.randomUUID().toString();var identified=supervision().adoptSandbox(id,SUPERVISOR,sandbox);
        code("sandbox_state_conflict",()->supervision().adoptSandbox(id,SUPERVISOR,UUID.randomUUID().toString()));
        code("cleanup_unverified",()->supervision().release(id,SUPERVISOR,proof(unknown,"stopped_unit",BOOT)));
        var released=supervision().release(id,SUPERVISOR,proof(identified,"stopped_unit",BOOT));
        assertEquals("released",released.sandboxPhase());assertEquals("released",released.leaseState());assertEquals("cancelled",released.state());
    }

    @Test void supervisedProofAuditFailureRollsBackProofSandboxAndExecutionQuotaTogether() {
        actors();deployment=skillDeployment();
        var execution=supervised("release-rollback");var lease=execution.lease();String id=lease.leaseId();
        supervision().begin(id,SUPERVISOR);supervision().observed(id,SUPERVISOR,INVOCATION);
        journal().reserve(lease,"gvisor-demo",IMAGE);journal().creating(lease);
        journal().created(lease,UUID.randomUUID().toString());
        ledger.finish(lease,RunState.SUCCEEDED,null);
        var finished=supervision().fence(id,SUPERVISOR);assertEquals("succeeded",finished.state());
        var proof=proof(finished,"stopped_unit",BOOT);
        jdbc.execute("CREATE TRIGGER fail_execution_audit BEFORE INSERT ON oa_tenant_audit FOR EACH ROW BEGIN IF NEW.action='execution.release.verified' THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='synthetic failure'; END IF; END");
        assertThrows(org.springframework.dao.DataAccessException.class,()->supervision().release(id,SUPERVISOR,proof));
        var rolledBack=supervision().inspect(id,SUPERVISOR);
        assertEquals("observed",rolledBack.phase());assertEquals("created",rolledBack.sandboxPhase());assertEquals("cleanup_pending",rolledBack.leaseState());
        assertNull(jdbc.queryForObject("SELECT proof_id FROM oa_run_supervision WHERE lease_id=?",String.class,id));
        assertEquals(0,count("oa_skill_verification"));
        assertEquals(0,jdbc.queryForObject("SELECT COUNT(*) FROM oa_tenant_audit WHERE action IN ('execution.release.verified','sandbox.release.verified')",Integer.class));
        jdbc.execute("DROP TRIGGER fail_execution_audit");
        assertEquals("succeeded",supervision().release(id,SUPERVISOR,proof).state());
        assertEquals(1,count("oa_skill_verification"));
        assertEquals(2,jdbc.queryForObject("SELECT COUNT(*) FROM oa_tenant_audit WHERE action IN ('execution.release.verified','sandbox.release.verified')",Integer.class));
    }

    @Test void supervisedRebootProofRequiresDifferentBootAndStillPreservesExistingTerminalState() {
        var execution=supervised("reboot");String id=execution.lease().leaseId();
        supervision().begin(id,SUPERVISOR);
        jdbc.update("UPDATE oa_run_attempt SET heartbeat_until=TIMESTAMPADD(SECOND,-1,CURRENT_TIMESTAMP(3)) WHERE lease_id=?",id);
        assertEquals("launching",supervision().inspect(id,SUPERVISOR).phase());
        assertEquals("failed",supervision().fence(id,SUPERVISOR).state());
        code("cleanup_unverified",()->supervision().release(id,SUPERVISOR,proof(execution,"host_reboot",BOOT)));
        assertEquals("released",supervision().release(id,SUPERVISOR,proof(execution,"host_reboot",UUID.randomUUID().toString())).leaseState());
        assertEquals("outcome_unknown",ledger.get(scope,execution.lease().runId()).get("error_code"));
    }

    @Test void supervisedIdentityAndScanCannotAccessOtherHostsOrLeakRuntimePayload() throws Exception {
        jdbc.update("UPDATE oa_tenant_environment SET max_concurrent_runs=4 WHERE tenant_id=? AND environment='test'",A);
        var first=supervised("scan-a");var second=supervised("scan-b");
        var other=supervision().claim(scope,submit("scan-other").runId(),OTHER_SUPERVISOR,BOOT).execution();
        String id=first.lease().leaseId();
        code("resource_unavailable",()->supervision().inspect(id,OTHER_SUPERVISOR));
        code("resource_unavailable",()->supervision().begin(id,OTHER_SUPERVISOR));
        code("resource_unavailable",()->supervision().observed(id,OTHER_SUPERVISOR,INVOCATION));
        code("resource_unavailable",()->supervision().fence(id,OTHER_SUPERVISOR));
        code("resource_unavailable",()->supervision().adoptSandbox(id,OTHER_SUPERVISOR,UUID.randomUUID().toString()));
        code("resource_unavailable",()->supervision().release(id,OTHER_SUPERVISOR,proof(first,"never_launched",BOOT)));
        code("action_forbidden",()->supervision().inspect(id,"worker-1"));
        var page=supervision().pending(SUPERVISOR,null,1);
        var next=supervision().pending(SUPERVISOR,page.getFirst().lease().leaseId(),1);
        assertEquals(1,page.size());assertEquals(1,next.size());
        assertNotEquals(page.getFirst().lease().leaseId(),next.getFirst().lease().leaseId());
        assertTrue(supervision().pending(SUPERVISOR,next.getFirst().lease().leaseId(),1).isEmpty());
        assertEquals(other.lease(),supervision().pending(OTHER_SUPERVISOR,null,100).getFirst().lease());
        String body=new ObjectMapper().findAndRegisterModules().writeValueAsString(page);
        assertFalse(body.contains("hello"));assertFalse(body.contains("Synthetic test only"));
        supervision().fence(id,SUPERVISOR);supervision().release(id,SUPERVISOR,proof(first,"never_launched",BOOT));
        assertEquals(second.lease(),supervision().pending(SUPERVISOR,null,100).getFirst().lease());
    }

    @Test void supervisedProofRejectsInvocationMismatchAndIncompletePhysicalAssertions() {
        var execution=supervised("invalid-proof");String id=execution.lease().leaseId();
        code("supervision_conflict",()->supervision().observed(id,SUPERVISOR,INVOCATION));
        assertThrows(org.springframework.dao.DataAccessException.class,()->jdbc.update("UPDATE oa_run_supervision SET phase='observed' WHERE lease_id=?",id));
        supervision().begin(id,SUPERVISOR);var observed=supervision().observed(id,SUPERVISOR,INVOCATION);supervision().fence(id,SUPERVISOR);
        code("cleanup_unverified",()->supervision().release(id,SUPERVISOR,proof(execution,"stopped_unit",BOOT)));
        var proof=proof(observed,"stopped_unit",BOOT);
        var incomplete=new RunSupervision.Proof(proof.proofId(),proof.kind(),proof.bootId(),proof.unitName(),proof.invocationId(),null,proof.evidenceSha256(),true,false,true,true);
        code("invalid_cleanup_proof",()->supervision().release(id,SUPERVISOR,incomplete));
        assertEquals("cleanup_pending",supervision().inspect(id,SUPERVISOR).leaseState());
    }

    @Test void sandboxAdmissionRejectsUnboundImageProductionDemoAndForeignOwner() {
        var lease = claim(submit("image").runId());
        code("invalid_sandbox_configuration", () -> journal().reserve(lease,"gvisor-demo",IMAGE));
        bindSkill(scope, deployment);
        code("invalid_sandbox_configuration", () -> journal().reserve(lease,"gvisor-demo",IMAGE.replace('a','b')));
        code("invalid_sandbox_configuration", () -> journal().reserve(lease,"gvisor-demo","registry.invalid/image:latest"));
        var foreign = new RunLedger.Lease(lease.scope(),lease.runId(),1,lease.leaseId(),lease.fencingToken(),"other-worker");
        code("lease_fenced", () -> journal().reserve(foreign,"gvisor-demo",IMAGE));
        var prodScope = new RunLedger.Scope(A,"prod");
        var prodDeployment = seedDeployment(prodScope); bindSkill(prodScope,prodDeployment);
        var prodRun = ledger.submit(prodScope,prodDeployment,"alice","prod",json.createObjectNode(),60,()->{});
        var prod = ledger.claim(prodScope,prodRun.runId(),"worker-1").orElseThrow();
        code("invalid_sandbox_configuration", () -> journal().reserve(prod,"gvisor-demo",IMAGE));
        journal().reserve(prod,"kata",IMAGE);
        assertEquals(1, count("oa_run_sandbox"));
    }

    @Test void sandboxDatabaseConstraintsRejectScopeLeaseMismatchAndUnverifiedRelease() {
        bindSkill(scope,deployment);
        var lease = reserved("constraint");
        var second = claim(submit("another").runId());
        assertThrows(org.springframework.dao.DataAccessException.class,
                () -> jdbc.update("UPDATE oa_run_sandbox SET lease_id=? WHERE lease_id=?",second.leaseId(),lease.leaseId()));
        assertThrows(org.springframework.dao.DataAccessException.class,
                () -> jdbc.update("UPDATE oa_run_sandbox SET phase='released' WHERE lease_id=?",lease.leaseId()));
        assertThrows(org.springframework.dao.DataAccessException.class,
                () -> jdbc.update("UPDATE oa_run_sandbox SET environment='prod' WHERE lease_id=?",lease.leaseId()));
    }

    @Test void lostCreationAndWorkerDestructionReportsKeepBothQuotasUntilVerified() {
        bindSkill(scope,deployment);
        jdbc.update("UPDATE oa_tenant_environment SET max_sandboxes=1 WHERE tenant_id=? AND environment='test'",A);
        var lease = reserved("ambiguous"); journal().creating(lease);
        ledger.cancel(scope,lease.runId(),"alice",()->{});
        ledger.finish(lease,RunState.FAILED,"execution_failed");
        code("sandbox_cleanup_pending", () -> ledger.resourcesReleased(lease));
        var waiting = claim(submit("waiting").runId());
        code("sandbox_quota_full", () -> journal().reserve(waiting,"gvisor-demo",IMAGE));
        String sandboxId = UUID.randomUUID().toString();
        // ID can arrive after cancellation, even when the initial created callback was lost.
        journal().destructionReported(lease,sandboxId);
        journal().created(lease,sandboxId);
        assertEquals("cleanup_pending",sandboxPhase(lease));
        assertNotNull(jdbc.queryForObject("SELECT destruction_reported_at FROM oa_run_sandbox WHERE lease_id=?",Timestamp.class,lease.leaseId()));
        code("sandbox_cleanup_pending", () -> ledger.resourcesReleased(lease));
        code("sandbox_quota_full", () -> journal().reserve(waiting,"gvisor-demo",IMAGE));
        String evidence = UUID.randomUUID().toString();
        journal().releaseVerified(lease,"synthetic-verifier",evidence);
        journal().releaseVerified(lease,"synthetic-verifier",evidence);
        journal().created(lease,sandboxId);
        assertEquals("released",sandboxPhase(lease));
        assertEquals("released",jdbc.queryForObject("SELECT lease_state FROM oa_run_attempt WHERE lease_id=?",String.class,lease.leaseId()));
        assertEquals(1,jdbc.queryForObject("SELECT COUNT(*) FROM oa_tenant_audit WHERE action='sandbox.release.verified'",Integer.class));
        journal().reserve(waiting,"gvisor-demo",IMAGE);
    }

    @Test void cleanupCallbacksRemainOwnerBoundAfterCancellationAndNeverRebindSandbox() {
        bindSkill(scope,deployment);
        var lease = reserved("callback"); journal().creating(lease);
        var foreign = new RunLedger.Lease(lease.scope(),lease.runId(),1,lease.leaseId(),lease.fencingToken(),"other-worker");
        String sandboxId = UUID.randomUUID().toString();
        code("lease_fenced", () -> journal().created(foreign,sandboxId));
        var wrongScope = new RunLedger.Lease(new RunLedger.Scope(B,"test"),lease.runId(),1,lease.leaseId(),lease.fencingToken(),lease.workerId());
        code("resource_unavailable", () -> journal().destructionReported(wrongScope,sandboxId));
        journal().created(lease,sandboxId);
        code("sandbox_state_conflict", () -> journal().cleanupPending(lease,UUID.randomUUID().toString()));
        jdbc.update("UPDATE oa_tenant SET status='suspended' WHERE tenant_id=?",A);
        jdbc.update("UPDATE oa_run_attempt SET heartbeat_until=TIMESTAMPADD(SECOND,-1,CURRENT_TIMESTAMP(3)) WHERE lease_id=?",lease.leaseId());
        journal().cleanupPending(lease,sandboxId); journal().destructionReported(lease,sandboxId);
        assertEquals("cleanup_pending",sandboxPhase(lease));
        code("lease_fenced", () -> journal().creating(lease));
    }

    @Test void verifiedReleaseRequiresTerminalAndRollsBackBothQuotasOnAuditFailure() {
        bindSkill(scope,deployment);
        var lease = reserved("audit");
        code("cleanup_before_terminal", () -> journal().releaseVerified(lease,"synthetic-verifier",UUID.randomUUID().toString()));
        assertEquals("reserved",sandboxPhase(lease));
        ledger.finish(lease,RunState.FAILED,"execution_failed");
        jdbc.execute("CREATE TRIGGER fail_cleanup_audit BEFORE INSERT ON oa_tenant_audit FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='synthetic failure'");
        assertThrows(org.springframework.dao.DataAccessException.class, () -> journal().releaseVerified(lease,"synthetic-verifier",UUID.randomUUID().toString()));
        assertEquals("reserved",sandboxPhase(lease));
        assertEquals("cleanup_pending",jdbc.queryForObject("SELECT lease_state FROM oa_run_attempt WHERE lease_id=?",String.class,lease.leaseId()));
        jdbc.execute("DROP TRIGGER fail_cleanup_audit");
        journal().releaseVerified(lease,"synthetic-verifier",UUID.randomUUID().toString());
        assertEquals("released",sandboxPhase(lease));
        code("sandbox_state_conflict", () -> journal().created(lease,UUID.randomUUID().toString()));
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
        bindSkill(scope, deployment);
        var execution = supervised("restart-sandbox");
        var sandboxLease = execution.lease();
        supervision().begin(sandboxLease.leaseId(),SUPERVISOR);
        supervision().observed(sandboxLease.leaseId(),SUPERVISOR,INVOCATION);
        journal().reserve(sandboxLease,"gvisor-demo",IMAGE);
        journal().creating(sandboxLease);
        var signal = java.nio.file.Path.of(System.getenv("OA_RUN_TEST_RESTART_SIGNAL"));
        java.nio.file.Files.writeString(signal, "restart");
        long until = System.nanoTime() + TimeUnit.SECONDS.toNanos(60);
        while (!java.nio.file.Files.readString(signal).equals("ready") && System.nanoTime() < until) Thread.sleep(100);
        assertEquals("ready", java.nio.file.Files.readString(signal), "Isolated database restart did not complete");
        assertEquals("queued", instance().get(scope, id).get("state"));
        assertEquals(1, instance().events(scope, id, 0, 20).events().size());
        assertEquals(2, count("oa_run_outbox"));
        assertEquals(id, submit("restart").runId());
        assertTrue(ledger.claim(scope, id, "after-restart").isPresent());
        assertEquals("creating", sandboxPhase(sandboxLease));
        var restored = supervision().inspect(sandboxLease.leaseId(),SUPERVISOR);
        assertEquals("observed",restored.phase());assertEquals(INVOCATION,restored.invocationId());
        assertEquals(BOOT,restored.bootId());assertEquals(execution.unitName(),restored.unitName());
        assertEquals("held",restored.leaseState());
        code("supervision_conflict",()->supervision().begin(sandboxLease.leaseId(),SUPERVISOR));
        code("sandbox_state_conflict", () -> journal().creating(sandboxLease));
        assertTrue(ledger.claim(scope, sandboxLease.runId(), "after-restart").isEmpty());
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
