package com.iflytek.astron.console.hub.platform.run;

import java.io.InputStream;
import java.util.*;
import org.springframework.core.env.Environment;
import org.springframework.core.io.Resource;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.support.TransactionTemplate;

@Service
public class TaskOutputService {
    private static final String PUBLIC="output_id,attempt_no,step_id,source_path,file_name,size_bytes,sha256,state,created_at,published_at";
    private final RunLedger ledger;
    private final JdbcTemplate jdbc;
    private final TaskOutputStore store;
    private final DeploymentAuthorization authorization;
    private final AgentDeploymentService deployments;
    private final TransactionTemplate tx;
    private final long tenantBytes;
    public TaskOutputService(RunLedger ledger,JdbcTemplate jdbc,TaskOutputStore store,DeploymentAuthorization authorization,
            AgentDeploymentService deployments,PlatformTransactionManager manager,Environment env) {
        this.ledger=ledger;this.jdbc=jdbc;this.store=store;this.authorization=authorization;this.deployments=deployments;
        tx=new TransactionTemplate(manager);tx.setTimeout(15);
        tenantBytes=env.getProperty("platform.outputs.max-tenant-bytes",Long.class,2L*1024*1024*1024);
        if(tenantBytes<128L*1024*1024)throw new IllegalArgumentException("Invalid output quota");
    }
    public record Descriptor(String path,long size,String sha256,String step) {}
    static void validate(Descriptor value) {
        if(value==null || value.path()==null || !value.path().startsWith("/workspace/outputs/") || value.path().length()>512
                || value.path().chars().anyMatch(c->c<32 || c==127) || value.path().contains("\\")
                || Arrays.stream(value.path().substring(1).split("/",-1)).anyMatch(s->s.isEmpty()||s.equals(".")||s.equals("..")||s.length()>255)
                || value.size()<0 || value.size()>TaskOutputStore.MAX_FILE_BYTES || value.sha256()==null || !value.sha256().matches("[a-f0-9]{64}")
                || value.step()==null || !value.step().matches("[A-Za-z0-9_.:-]{1,128}"))throw new RunFailure("invalid_output");
    }
    public Map<String,Object> reserve(RunLedger.Lease lease,Descriptor value) {
        validate(value);String fingerprint=RunJson.hash(value.path()+"\n"+value.sha256());
        return ledger.withPermit(lease,()-> {
            var existing=jdbc.queryForList("SELECT "+PUBLIC+" FROM oa_run_output WHERE tenant_id=? AND environment=? AND run_id=? AND fingerprint=?",
                    lease.scope().tenant(),lease.scope().environment(),lease.runId(),fingerprint);
            if(!existing.isEmpty()) {
                if(((Number)existing.getFirst().get("size_bytes")).longValue()!=value.size())throw new RunFailure("invalid_output_size");
                return existing.getFirst();
            }
            var task=jdbc.queryForMap("SELECT COUNT(*) count,COALESCE(SUM(size_bytes),0) bytes FROM oa_run_output WHERE run_id=?",lease.runId());
            long occupied=jdbc.queryForObject("SELECT COALESCE(SUM(size_bytes),0) FROM oa_run_output WHERE tenant_id=? AND environment=?",Long.class,lease.scope().tenant(),lease.scope().environment());
            if(((Number)task.get("count")).longValue()>=32 || ((Number)task.get("bytes")).longValue()+value.size()>128L*1024*1024
                    || occupied+value.size()>tenantBytes)throw new RunFailure("output_quota_exceeded");
            String id=UUID.randomUUID().toString();
            jdbc.update("""
                    INSERT INTO oa_run_output(output_id,tenant_id,environment,run_id,attempt_no,step_id,source_path,file_name,size_bytes,sha256,fingerprint)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?)
                    """,id,lease.scope().tenant(),lease.scope().environment(),lease.runId(),lease.attempt(),value.step(),value.path(),
                    value.path().substring(value.path().lastIndexOf('/')+1),value.size(),value.sha256(),fingerprint);
            return jdbc.queryForMap("SELECT "+PUBLIC+" FROM oa_run_output WHERE output_id=?",id);
        });
    }
    public Map<String,Object> upload(RunLedger.Lease lease,String id,InputStream input,long length) {
        String upload=UUID.randomUUID().toString();String key="task-outputs/v1/"+lease.runId()+"/"+id+"/"+upload;
        var metadata=ledger.withPermit(lease,()-> {
            var row=owned(lease.runId(),id);
            if("available".equals(row.get("state")))throw new RunFailure("output_already_published");
            if(length!=((Number)row.get("size_bytes")).longValue())throw new RunFailure("invalid_output_size");
            if(jdbc.queryForObject("SELECT COUNT(*) FROM oa_run_output_upload WHERE output_id=? AND state='writing'",Integer.class,id)>0)
                throw new RunFailure("output_transfer_busy");
            if(jdbc.queryForObject("SELECT COUNT(*) FROM oa_run_output_upload WHERE output_id=?",Integer.class,id)>=5)
                throw new RunFailure("output_retry_exhausted");
            jdbc.update("INSERT INTO oa_run_output_upload(upload_id,output_id,object_key) VALUES(?,?,?)",upload,id,key);
            jdbc.update("UPDATE oa_run_output SET state='pending' WHERE output_id=?",id);return row;
        });
        try {
            store.put(key,input,length,(String)metadata.get("sha256"));
            return ledger.withPermit(lease,()-> {
                jdbc.queryForMap("SELECT output_id FROM oa_run_output WHERE output_id=? FOR UPDATE",id);
                if(jdbc.update("UPDATE oa_run_output_upload SET state='published' WHERE upload_id=? AND state='writing'",upload)!=1)
                    throw new RunFailure("output_upload_expired");
                jdbc.update("UPDATE oa_run_output SET state='available',object_key=?,published_at=CURRENT_TIMESTAMP(3) WHERE output_id=?",key,id);
                return jdbc.queryForMap("SELECT "+PUBLIC+" FROM oa_run_output WHERE output_id=?",id);
            });
        } catch(RuntimeException failure) {
            // Ambiguous commit: never delete a key already referenced by a published row.
            tx.execute(status->{
                jdbc.queryForMap("SELECT output_id FROM oa_run_output WHERE output_id=? FOR UPDATE",id);
                if(jdbc.queryForObject("SELECT COUNT(*) FROM oa_run_output WHERE object_key=?",Integer.class,key)==0) {
                    jdbc.update("UPDATE oa_run_output_upload SET state='abandoned',reconcile_after=CURRENT_TIMESTAMP(3) WHERE upload_id=? AND state IN ('writing','reaping','cleared')",upload);
                    jdbc.update("UPDATE oa_run_output SET state='failed' WHERE output_id=? AND state<>'available'",id);
                }return null;
            });throw failure;
        }
    }
    private Map<String,Object> owned(String run,String id) {
        var rows=jdbc.queryForList("SELECT * FROM oa_run_output WHERE run_id=? AND output_id=?",run,id);
        if(rows.isEmpty())throw new RunFailure("resource_unavailable");return rows.getFirst();
    }
    private <T> T authorized(DeploymentAuthorization.Caller caller,String run,java.util.function.Supplier<T> action) {
        var deployment=authorization.runDeployment(run);
        return tx.execute(status->{deployments.lock(deployment.scope());authorization.require(caller,deployment,DeploymentAuthorization.Action.READ,run);return action.get();});
    }
    public List<Map<String,Object>> list(DeploymentAuthorization.Caller caller,String run) {
        return authorized(caller,run,()->jdbc.queryForList("SELECT "+PUBLIC+" FROM oa_run_output WHERE run_id=? ORDER BY created_at,output_id",run));
    }
    public record Download(Resource content,String name,long size,String sha256) {}
    public Download download(DeploymentAuthorization.Caller caller,String run,String id,boolean preview) {
        var row=authorized(caller,run,()->owned(run,id));
        if(!"available".equals(row.get("state")))throw new RunFailure("output_not_available");
        long size=((Number)row.get("size_bytes")).longValue();String name=(String)row.get("file_name");
        if(preview && (size>256*1024 || !name.toLowerCase(Locale.ROOT).matches(".*\\.(txt|md|csv|json|log|yaml|yml)")))throw new RunFailure("output_preview_unavailable");
        var resource=store.verified((String)row.get("object_key"),size,(String)row.get("sha256"));
        try {
            authorized(caller,run,()->{jdbc.update("INSERT INTO oa_run_output_access(output_id,actor_id,action) VALUES(?,?,?)",id,caller.actor(),preview?"preview":"download");return null;});
            return new Download(resource,name,size,(String)row.get("sha256"));
        } catch(RuntimeException denied) { try {resource.getInputStream().close();}catch(Exception ignored){}throw denied; }
    }
    /** Keep exact-key tombstones: an old uploader may commit after an earlier successful delete. */
    @Scheduled(fixedDelay=60000,initialDelay=60000)
    public void reconcile() {
        for(var row:jdbc.queryForList("SELECT upload_id,output_id,object_key FROM oa_run_output_upload WHERE state IN ('writing','abandoned','reaping','cleared') AND created_at<TIMESTAMPADD(MINUTE,-15,CURRENT_TIMESTAMP(3)) AND reconcile_after<=CURRENT_TIMESTAMP(3) ORDER BY reconcile_after,upload_id LIMIT 20")) {
            try {
                boolean reap=Boolean.TRUE.equals(tx.execute(status->{
                    jdbc.queryForMap("SELECT output_id FROM oa_run_output WHERE output_id=? FOR UPDATE",row.get("output_id"));
                    if(jdbc.queryForObject("SELECT COUNT(*) FROM oa_run_output WHERE object_key=?",Integer.class,row.get("object_key"))!=0)return false;
                    if(jdbc.update("UPDATE oa_run_output_upload SET state='reaping',reconcile_after=TIMESTAMPADD(MINUTE,5,CURRENT_TIMESTAMP(3)) WHERE upload_id=? AND state IN ('writing','abandoned','reaping','cleared') AND reconcile_after<=CURRENT_TIMESTAMP(3)",row.get("upload_id"))!=1)return false;
                    jdbc.update("UPDATE oa_run_output SET state='failed' WHERE output_id=? AND state='pending'",row.get("output_id"));return true;
                }));
                if(reap) {store.remove((String)row.get("object_key"));jdbc.update("UPDATE oa_run_output_upload SET state='cleared',reconcile_after=TIMESTAMPADD(DAY,1,CURRENT_TIMESTAMP(3)) WHERE upload_id=? AND state='reaping'",row.get("upload_id"));}
            } catch(RuntimeException failure) {org.slf4j.LoggerFactory.getLogger(getClass()).warn("Task output reconciliation deferred; upload={}",row.get("upload_id"));}
        }
    }
}
