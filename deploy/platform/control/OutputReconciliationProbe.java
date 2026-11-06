import com.fasterxml.jackson.databind.ObjectMapper;
import com.iflytek.astron.console.hub.platform.run.*;
import io.minio.*;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.security.MessageDigest;
import java.util.*;
import java.util.regex.Pattern;
import org.springframework.core.env.*;
import org.springframework.core.io.*;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.*;
import org.springframework.jdbc.datasource.init.ResourceDatabasePopulator;

/** Real service/ledger/transactions and S3; synthetic identity, no HTTP/auth coverage. */
public class OutputReconciliationProbe {
    static final String TENANT="output-fixture", BUCKET="output-reconciliation-fixture";
    static final byte[] CONTENT="Task output recovery: 文件\n".getBytes(StandardCharsets.UTF_8);
    final ObjectMapper json=new ObjectMapper();
    final RunLedger.Scope scope=new RunLedger.Scope(TENANT,"test");
    final DriverManagerDataSource source;
    final JdbcTemplate jdbc;
    final RunLedger ledger;
    final MinioTaskOutputStore store;
    final TaskOutputService outputs;
    final MinioClient s3;

    static void require(boolean condition) {if(!condition)throw new IllegalStateException("fixture_assertion_failed");}
    static String hash(byte[] data) throws Exception {return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(data));}
    interface Operation {void run() throws Exception;}
    static void rejected(Operation action,String code) throws Exception {
        try {action.run();}catch(RunFailure failure) {require(code.equals(failure.code()));return;}
        throw new IllegalStateException("fixture_expected_failure");
    }
    OutputReconciliationProbe() {
        String url=System.getenv("FIXTURE_JDBC_URL"), endpoint=System.getenv("FIXTURE_ENDPOINT");
        String key=System.getenv("FIXTURE_KEY"),secret=System.getenv("FIXTURE_SECRET");
        require("jdbc:mysql://127.0.0.1:3306/oa_output_fixture?useSSL=false&allowPublicKeyRetrieval=true&connectTimeout=5000&socketTimeout=15000".equals(url));
        require("http://127.0.0.1:8333".equals(endpoint) && key.startsWith("fixture_"));
        source=new DriverManagerDataSource(url,"fixture_output",System.getenv("FIXTURE_DB_PASSWORD"));
        jdbc=new JdbcTemplate(source);
        var tx=new DataSourceTransactionManager(source);
        ledger=new RunLedger(jdbc,tx,json);
        var env=new StandardEnvironment();
        env.getPropertySources().addFirst(new MapPropertySource("fixture",Map.of("s3.endpoint",endpoint,
            "s3.accessKey",key,"s3.secretKey",secret,"s3.bucket",BUCKET)));
        store=new MinioTaskOutputStore(env);
        // Only worker reserve/upload and scheduled reconciliation are invoked.
        // User-facing authorization methods are intentionally outside this probe.
        outputs=new TaskOutputService(ledger,jdbc,store,null,null,tx,env);
        s3=MinioClient.builder().endpoint(endpoint).credentials(key,secret).build();
    }
    static ByteArrayResource table(String filename,String table) throws Exception {
        String sql=new ClassPathResource("db/migration/"+filename).getContentAsString(StandardCharsets.UTF_8);
        var match=Pattern.compile("CREATE TABLE `"+table+"`\\s*\\(.*?\\) ENGINE=.*?;",Pattern.DOTALL).matcher(sql);
        require(match.find());return new ByteArrayResource(match.group().getBytes(StandardCharsets.UTF_8));
    }
    void initialize() throws Exception {
        var scripts=new ResourceDatabasePopulator();
        scripts.addScript(table("V1.1__init_core.sql","user_info"));
        scripts.addScript(table("V1.3__init_space.sql","agent_space"));
        scripts.addScript(table("V1.3__init_space.sql","agent_space_user"));
        var migrations=new TreeMap<Integer,Path>();
        try(var files=Files.list(Path.of("/fixture/classes/db/migration"))) {
            files.forEach(path->{
                var match=Pattern.compile("V1\\.(\\d+)__.*\\.sql").matcher(path.getFileName().toString());
                if(match.matches()) {int version=Integer.parseInt(match.group(1));if(version>=49 && version<=69)migrations.put(version,path);}
            });
        }
        require(migrations.size()==21);
        for(var path:migrations.values())scripts.addScript(new FileSystemResource(path));
        scripts.execute(source);
        jdbc.update("INSERT INTO oa_tenant(tenant_id,display_name,scope_kind,space_id) VALUES(?,?,'space',10)",TENANT,TENANT);
        jdbc.update("INSERT INTO oa_tenant_environment(tenant_id,environment,max_concurrent_runs,max_queued_runs) VALUES(?,'test',8,20)",TENANT);
        String model=UUID.randomUUID().toString(),deployment=UUID.randomUUID().toString();
        jdbc.update("INSERT INTO oa_model_connection(tenant_id,environment,connection_id,display_name,current_version,created_by) VALUES(?,'test',?,'Fixture model',1,'fixture')",TENANT,model);
        jdbc.update("""
            INSERT INTO oa_model_connection_version(tenant_id,environment,connection_id,version,endpoint,model_name,
              supports_stream,supports_tools,supports_usage,connect_timeout_seconds,read_timeout_seconds,total_timeout_seconds,
              credential_ref,credential_key_id,credential_nonce,credential_cipher)
            VALUES(?,'test',?,1,'https://model.invalid/v1','synthetic',true,true,true,5,60,120,?,'fixture',?,?)
            """,TENANT,model,UUID.randomUUID().toString(),new byte[12],new byte[32]);
        String snapshot="{\"systemPrompt\":\"Synthetic recovery fixture\"}";
        jdbc.update("""
            INSERT INTO oa_agent_deployment(tenant_id,environment,deployment_id,display_name,executor_kind,agent_version,
              model_connection_id,model_version,snapshot_json,snapshot_sha256,state,created_by)
            VALUES(?,'test',?,'Recovery fixture','deepagents','v1',?,1,?,?,'test','fixture')
            """,TENANT,deployment,model,snapshot,hash(snapshot.getBytes(StandardCharsets.UTF_8)));
        s3.makeBucket(MakeBucketArgs.builder().bucket(BUCKET).build());
    }
    RunLedger.Lease lease(String key) {
        String deployment=jdbc.queryForObject("SELECT deployment_id FROM oa_agent_deployment",String.class);
        String run=ledger.submit(scope,deployment,"fixture",key,json.createObjectNode().put("message","fixture"),3600,()->{}).runId();
        return ledger.claim(scope,run,"fixture-worker").orElseThrow();
    }
    String reserve(RunLedger.Lease lease,String name) throws Exception {
        return (String)outputs.reserve(lease,new TaskOutputService.Descriptor("/workspace/outputs/"+name,CONTENT.length,hash(CONTENT),"fixture")).get("output_id");
    }
    Map<String,Object> output(String name) {return jdbc.queryForMap("SELECT * FROM oa_run_output WHERE file_name=?",name);}
    String candidateKey() {return jdbc.queryForObject("SELECT u.object_key FROM oa_run_output_upload u JOIN oa_run_output o ON o.output_id=u.output_id WHERE o.file_name='crash.txt'",String.class);}
    String lateKey() {return jdbc.queryForObject("SELECT u.object_key FROM oa_run_output_upload u JOIN oa_run_output o ON o.output_id=u.output_id WHERE o.file_name='late.txt'",String.class);}
    static void waitForRelease() {
        while(!Files.exists(Path.of("/fixture/late-release"))) {
            try {Thread.sleep(50);}catch(InterruptedException e) {Thread.currentThread().interrupt();throw new IllegalStateException("fixture_interrupted");}
        }
    }
    void same(String key) throws Exception {
        try(var input=store.verified(key,CONTENT.length,hash(CONTENT)).getInputStream()) {require(Arrays.equals(CONTENT,input.readAllBytes()));}
    }
    void retained() throws Exception {
        var keep=output("keep.txt");require("available".equals(keep.get("state")));same((String)keep.get("object_key"));
        require(jdbc.queryForObject("SELECT state FROM oa_run_output_upload WHERE output_id=?",String.class,keep.get("output_id")).equals("published"));
    }
    void ageUploads() {jdbc.update("UPDATE oa_run_output_upload SET created_at=TIMESTAMPADD(MINUTE,-20,CURRENT_TIMESTAMP(3)),reconcile_after=TIMESTAMPADD(SECOND,-1,CURRENT_TIMESTAMP(3))");}
    AgentRunService deletionApi() {
        var membership=new com.iflytek.astron.console.commons.tenant.TenantAccessRepository(jdbc);
        var authorization=new DeploymentAuthorization(jdbc,membership);
        var manager=new DataSourceTransactionManager(source);
        var deployments=new AgentDeploymentService(jdbc,new com.iflytek.astron.console.commons.tenant.TenantAccessService(membership),json,manager);
        return new AgentRunService(ledger,authorization,deployments,jdbc,json,new StandardEnvironment(),manager);
    }
    void execute(String mode) throws Exception {
        if(mode.equals("prepare")) {
            initialize();var lease=lease("keep");String id=reserve(lease,"keep.txt");
            outputs.upload(lease,id,new ByteArrayInputStream(CONTENT),CONTENT.length);retained();
        } else if(mode.equals("crash-upload")) {
            var lease=lease("crash");String id=reserve(lease,"crash.txt");
            var input=new ByteArrayInputStream(CONTENT) {
                @Override public synchronized int read() {
                    require(available()==0);
                    System.out.println("OUTPUT_RECONCILIATION_READY=crash-upload");System.out.flush();
                    try {new java.util.concurrent.CountDownLatch(1).await();}
                    catch(InterruptedException e) {Thread.currentThread().interrupt();throw new IllegalStateException("fixture_interrupted");}
                    throw new IllegalStateException("fixture_unexpected_resume");
                }
            };
            outputs.upload(lease,id,input,CONTENT.length);
            throw new IllegalStateException("fixture_crash_not_injected");
        } else if(mode.equals("candidate-present")) {
            require("pending".equals(output("crash.txt").get("state")));
            require(jdbc.queryForObject("SELECT state FROM oa_run_output_upload WHERE object_key=?",String.class,candidateKey()).equals("writing"));
            same(candidateKey());retained();
        } else if(mode.equals("outage")) {
            ageUploads();outputs.reconcile();
            require("failed".equals(output("crash.txt").get("state")));
            require(jdbc.queryForObject("SELECT state FROM oa_run_output_upload WHERE object_key=?",String.class,candidateKey()).equals("reaping"));
            require("available".equals(output("keep.txt").get("state")));
            var lease=lease("outage");String id=reserve(lease,"outage.txt");
            rejected(()->outputs.upload(lease,id,new ByteArrayInputStream(CONTENT),CONTENT.length),"output_storage_unavailable");
            require("failed".equals(output("outage.txt").get("state")) && output("outage.txt").get("object_key")==null);
            require(jdbc.queryForObject("SELECT state FROM oa_run_output_upload WHERE output_id=?",String.class,id).equals("abandoned"));
        } else if(mode.equals("recover")) {
            ageUploads();outputs.reconcile();outputs.reconcile();
            require(jdbc.queryForObject("SELECT COUNT(*) FROM oa_run_output_upload WHERE state='cleared'",Integer.class)==2);
            rejected(()->store.verified(candidateKey(),CONTENT.length,hash(CONTENT)),"output_storage_unavailable");
            retained();
            var lease=lease("retry");String id=reserve(lease,"retry.txt");byte[] wrong=CONTENT.clone();wrong[0]^=1;
            rejected(()->outputs.upload(lease,id,new ByteArrayInputStream(wrong),wrong.length),"output_integrity_failed");
            require("failed".equals(output("retry.txt").get("state")) && output("retry.txt").get("object_key")==null);
            String failedKey=jdbc.queryForObject("SELECT object_key FROM oa_run_output_upload WHERE output_id=?",String.class,id);
            outputs.upload(lease,id,new ByteArrayInputStream(CONTENT),CONTENT.length);
            String published=(String)output("retry.txt").get("object_key");require(!failedKey.equals(published));
            ageUploads();outputs.reconcile();outputs.reconcile();
            require(jdbc.queryForObject("SELECT COUNT(*) FROM oa_run_output_upload WHERE state='cleared'",Integer.class)==3);
            require(jdbc.queryForObject("SELECT COUNT(*) FROM oa_run_output_upload WHERE state='published'",Integer.class)==2);
            require(jdbc.queryForObject("SELECT COUNT(*) FROM oa_run_output WHERE state='available'",Integer.class)==2);
            rejected(()->store.verified(failedKey,CONTENT.length,hash(wrong)),"output_storage_unavailable");
            same(published);retained();
        } else if(mode.equals("late-upload")) {
            var lease=lease("late");String id=reserve(lease,"late.txt");
            var input=new ByteArrayInputStream(CONTENT) {
                boolean started;
                @Override public synchronized int read(byte[] block,int offset,int length) {
                    if(!started) {started=true;System.out.println("OUTPUT_RECONCILIATION_READY=late-start");System.out.flush();waitForRelease();}
                    return super.read(block,offset,length);
                }
                @Override public synchronized int read() {
                    require(available()==0);System.out.println("OUTPUT_RECONCILIATION_READY=late-committed");System.out.flush();
                    try {new java.util.concurrent.CountDownLatch(1).await();}
                    catch(InterruptedException e) {Thread.currentThread().interrupt();throw new IllegalStateException("fixture_interrupted");}
                    throw new IllegalStateException("fixture_unexpected_resume");
                }
            };
            outputs.upload(lease,id,input,CONTENT.length);
            throw new IllegalStateException("fixture_crash_not_injected");
        } else if(mode.equals("late-reap")) {
            ageUploads();outputs.reconcile();
            require(jdbc.queryForObject("SELECT state FROM oa_run_output_upload WHERE object_key=?",String.class,lateKey()).equals("cleared"));
            require("failed".equals(output("late.txt").get("state")));
            rejected(()->store.verified(lateKey(),CONTENT.length,hash(CONTENT)),"output_storage_unavailable");retained();
        } else if(mode.equals("late-present")) {
            require(jdbc.queryForObject("SELECT state FROM oa_run_output_upload WHERE object_key=?",String.class,lateKey()).equals("cleared"));
            require(output("late.txt").get("object_key")==null);same(lateKey());retained();
        } else if(mode.equals("late-recover")) {
            // Advance only fixture scheduling timestamps, never production time.
            jdbc.update("UPDATE oa_run_output_upload SET reconcile_after=TIMESTAMPADD(SECOND,-1,CURRENT_TIMESTAMP(3))");
            outputs.reconcile();outputs.reconcile();
            rejected(()->store.verified(lateKey(),CONTENT.length,hash(CONTENT)),"output_storage_unavailable");retained();
            require(jdbc.queryForObject("SELECT COUNT(*) FROM oa_run_output_upload WHERE state='cleared'",Integer.class)==4);
            require(jdbc.queryForObject("SELECT COUNT(*) FROM oa_run_output_upload WHERE state='published'",Integer.class)==2);
        } else if(mode.equals("multipart-upload")) {
            byte[] data=new byte[12*1024*1024];Arrays.fill(data,(byte)17);
            var lease=lease("multipart");String id=(String)outputs.reserve(lease,new TaskOutputService.Descriptor("/workspace/outputs/multipart.bin",data.length,hash(data),"fixture")).get("output_id");
            var input=new ByteArrayInputStream(data) {
                @Override public synchronized int read(byte[] block,int offset,int length) {
                    if(pos>=6*1024*1024) {
                        System.out.println("OUTPUT_RECONCILIATION_READY=multipart-part");System.out.flush();
                        try {new java.util.concurrent.CountDownLatch(1).await();}
                        catch(InterruptedException e) {Thread.currentThread().interrupt();throw new IllegalStateException("fixture_interrupted");}
                    }
                    return super.read(block,offset,Math.min(length,65536));
                }
            };
            outputs.upload(lease,id,input,data.length);throw new IllegalStateException("fixture_crash_not_injected");
        } else if(mode.equals("multipart-key") || mode.equals("multipart-reap")) {
            var row=output("multipart.bin");require(row.get("object_key")==null);
            String key=jdbc.queryForObject("SELECT object_key FROM oa_run_output_upload WHERE output_id=?",String.class,row.get("output_id"));
            TaskOutputStore.checkKey(key);
            if(mode.equals("multipart-reap")) {
                ageUploads();outputs.reconcile();
                require(jdbc.queryForObject("SELECT state FROM oa_run_output_upload WHERE object_key=?",String.class,key).equals("cleared"));
            }
            System.out.println("OUTPUT_MULTIPART_KEY="+key);retained();
        } else if(mode.equals("delete-seed")) {
            jdbc.update("INSERT INTO user_info(uid,username,account_status,deleted) VALUES('output-admin','Fixture admin',1,0)");
            jdbc.update("INSERT INTO agent_space(id,name,uid,deleted) VALUES(10,'Fixture','output-admin',0)");
            jdbc.update("INSERT INTO agent_space_user(space_id,uid,role) VALUES(10,'output-admin',1)");
            jdbc.update("INSERT INTO oa_tenant_member(tenant_id,environment,uid,role) VALUES(?,'test','output-admin','tenant_admin')",TENANT);
            var lease=lease("delete-fixture");String id=reserve(lease,"deleted.txt");
            outputs.upload(lease,id,new ByteArrayInputStream(CONTENT),CONTENT.length);
            ledger.finish(lease,RunState.SUCCEEDED,null);ledger.resourcesReleased(lease);
            retained();
        } else if(mode.equals("delete-prepare")) {
            String run=(String)output("deleted.txt").get("run_id");
            var caller=new DeploymentAuthorization.Caller("output-admin",null);var api=deletionApi();
            require("pending".equals(api.delete(caller,run).get("files_status")));
            require("pending".equals(api.delete(caller,run).get("files_status")));
            rejected(()->api.status(caller,run),"resource_unavailable");
            rejected(()->api.events(caller,run,0),"resource_unavailable");
            require(jdbc.queryForObject("SELECT COUNT(*) FROM oa_tenant_audit WHERE action='run.deleted'",Integer.class)==1);
            retained();
        } else if(mode.equals("delete-after-snapshot")) {
            var lease=lease("deleted-after-snapshot");
            ledger.finish(lease,RunState.SUCCEEDED,null);ledger.resourcesReleased(lease);
            deletionApi().delete(new DeploymentAuthorization.Caller("output-admin",null),lease.runId());
        } else if(mode.equals("restore-deletions-check")) {
            String run=(String)output("deleted.txt").get("run_id");
            var caller=new DeploymentAuthorization.Caller("output-admin",null);var api=deletionApi();
            rejected(()->api.status(caller,run),"resource_unavailable");
            rejected(()->api.events(caller,run,0),"resource_unavailable");
            rejected(()->lease("delete-fixture"),"run_deleted");
            rejected(()->lease("deleted-after-snapshot"),"run_deleted");
            require(jdbc.queryForObject("SELECT COUNT(*) FROM oa_agent_run WHERE idempotency_key='deleted-after-snapshot'",Integer.class)==0);
            require(jdbc.queryForObject("SELECT COUNT(*) FROM oa_tenant_audit WHERE action='run.deletion_restored'",Integer.class)==1);
            require("pending".equals(api.deletion(caller,run).get("files_status")));retained();
        } else if(mode.equals("delete-outage") || mode.equals("delete-recover")) {
            var row=output("deleted.txt");String run=(String)row.get("run_id");
            jdbc.update("UPDATE oa_run_output_upload SET created_at=TIMESTAMPADD(MINUTE,-20,CURRENT_TIMESTAMP(3)),reconcile_after=TIMESTAMPADD(SECOND,-1,CURRENT_TIMESTAMP(3)) WHERE output_id=?",row.get("output_id"));
            outputs.reconcile();
            var receipt=deletionApi().deletion(new DeploymentAuthorization.Caller("output-admin",null),run);
            require((mode.equals("delete-outage")?"pending":"complete").equals(receipt.get("files_status")));
            if(mode.equals("delete-recover")) {
                String key=jdbc.queryForObject("SELECT object_key FROM oa_run_output_upload WHERE output_id=?",String.class,row.get("output_id"));
                try {s3.statObject(StatObjectArgs.builder().bucket(BUCKET).object(key).build());throw new IllegalStateException("deleted_object_remains");}
                catch(io.minio.errors.ErrorResponseException failure) {require("NoSuchKey".equals(failure.errorResponse().code()));}
                retained();
            }
        } else throw new IllegalArgumentException("unknown_fixture_mode");
    }
    public static void main(String[] args) {
        try {new OutputReconciliationProbe().execute(args[0]);System.out.println("OUTPUT_RECONCILIATION_OK="+args[0]);System.exit(0);}
        catch(Throwable failure) {
            System.err.println("OUTPUT_RECONCILIATION_FAILED="+failure.getClass().getSimpleName());
            for(Throwable cause=failure;cause!=null;cause=cause.getCause()) {
                System.err.println("OUTPUT_RECONCILIATION_CAUSE="+cause.getClass().getSimpleName());
                if(cause instanceof io.minio.errors.XmlParserException && cause.getMessage()!=null) {
                    String diagnostic=cause.getMessage().replaceAll("[A-Za-z0-9_+/=-]{24,}","[value]").replaceAll("[\\r\\n]"," ");
                    System.err.println("OUTPUT_RECONCILIATION_XML="+diagnostic.substring(0,Math.min(500,diagnostic.length())));
                }
                if(cause instanceof io.minio.errors.ErrorResponseException response) {
                    String code=response.errorResponse().code();
                    if(code!=null && code.matches("[A-Za-z0-9_]{1,80}"))System.err.println("OUTPUT_RECONCILIATION_S3_CODE="+code);
                }
            }
            System.exit(1);
        }
    }
}
