package com.iflytek.astron.console.hub.platform.run;

import com.fasterxml.jackson.annotation.JsonAnySetter;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.iflytek.astron.console.commons.tenant.*;
import jakarta.validation.Valid;
import jakarta.validation.constraints.*;
import java.util.*;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.TransactionDefinition;
import org.springframework.transaction.support.TransactionTemplate;

/** Console -> immutable deployment snapshot -> run ledger. No model or sandbox I/O here. */
@Service
public class AgentDeploymentService {
    static final String COLUMNS="tenant_id,environment,deployment_id,display_name,executor_kind,agent_version,"
            +"model_connection_id,model_version,snapshot_sha256,state,created_by,created_at";
    private final JdbcTemplate jdbc;
    private final TenantAccessService access;
    private final ObjectMapper json;
    private final TransactionTemplate tx;
    public AgentDeploymentService(JdbcTemplate jdbc,TenantAccessService access,ObjectMapper json,PlatformTransactionManager manager) {
        this.jdbc=jdbc;this.access=access;this.json=json;tx=new TransactionTemplate(manager);
        tx.setIsolationLevel(TransactionDefinition.ISOLATION_READ_COMMITTED);tx.setTimeout(15);
    }
    public record Execution(@NotBlank @Size(max=32768) String instructions, @Size(max=16384) String answerInstructions,
            @Min(1) @Max(30) Integer maxLoops, @DecimalMin("0") @DecimalMax("2") Double temperature,
            @Min(1) @Max(16384) Integer maxTokens) {
        @JsonAnySetter public void unknown(String name,JsonNode value) { throw new RunFailure("invalid_configuration"); }
        @Override public String toString() { return "Execution[content=REDACTED]"; }
    }
    public record Create(@NotBlank @Size(max=255) String displayName,
            @NotBlank @Pattern(regexp="[A-Za-z0-9_.-]{1,128}") String agentVersion,
            @NotNull UUID modelConnectionId,@Min(1) long modelVersion,
            @NotNull @Size(max=20) List<@NotNull UUID> skillReleaseIds,@NotNull @Valid Execution execution) {
        @JsonAnySetter public void unknown(String name,JsonNode value) { throw new RunFailure("invalid_configuration"); }
        @Override public String toString() { return "Create[configuration=REDACTED]"; }
    }
    public record Promote(@NotNull UUID sourceDeploymentId,@NotNull UUID modelConnectionId,@Min(1) long modelVersion,
            @NotNull UUID verificationRunId) {}

    public List<Map<String,Object>> list(RunLedger.Scope scope) {
        access.require(scope.tenant(),scope.environment(),TenantPermission.METADATA_READ);
        return jdbc.queryForList("SELECT "+COLUMNS+" FROM oa_agent_deployment WHERE tenant_id=? AND environment=? ORDER BY created_at DESC,deployment_id LIMIT 100",
                scope.tenant(),scope.environment());
    }

    public Map<String,Object> create(RunLedger.Scope scope,Create input) {
        if (!scope.environment().equals("test")) throw new RunFailure("production_promotion_required");
        return tx.execute(status->{
            lock(scope); var actor=access.require(scope.tenant(),"test",TenantPermission.RESOURCE_WRITE);
            ObjectNode execution=json.valueToTree(input.execution());
            execution.put("engine","deepagents");
            if (input.execution().maxLoops()==null) execution.put("maxLoops",10);
            if (input.execution().temperature()==null) execution.put("temperature",0.2);
            if (input.execution().maxTokens()==null) execution.put("maxTokens",4096);
            if (input.execution().answerInstructions()==null) execution.put("answerInstructions","");
            var releases=bindings(scope,input.skillReleaseIds());
            String id=insert(scope,input.displayName().trim(),input.agentVersion(),input.modelConnectionId().toString(),input.modelVersion(),execution,releases,actor.uid());
            audit(scope,actor.uid(),"deployment.created",id);
            return metadata(scope,id);
        });
    }

    public Map<String,Object> promote(String tenant,Promote input) {
        var scope=new RunLedger.Scope(tenant,"prod");
        return tx.execute(status->{
            lock(scope);
            var actor=access.require(tenant,"prod",TenantPermission.DEPLOYMENT_PUBLISH);
            access.require(tenant,"test",TenantPermission.METADATA_READ);
            String source=input.sourceDeploymentId().toString();
            var rows=jdbc.queryForList("SELECT * FROM oa_agent_deployment WHERE tenant_id=? AND environment='test' AND deployment_id=? AND state='test'",tenant,source);
            if(rows.isEmpty()) throw new RunFailure("resource_unavailable");
            var previous=rows.getFirst();
            String snapshot=(String)previous.get("snapshot_json");
            if(!RunJson.hash(snapshot).equals(previous.get("snapshot_sha256"))) throw new RunFailure("deployment_unavailable");
            if(!"deepagents".equals(previous.get("executor_kind"))) throw new RunFailure("executor_unavailable");
            if(jdbc.queryForList("SELECT run_id FROM oa_agent_run WHERE tenant_id=? AND environment='test' AND deployment_id=? AND run_id=? AND state='succeeded' AND snapshot_sha256=?",
                    tenant,source,input.verificationRunId().toString(),previous.get("snapshot_sha256")).isEmpty())
                throw new RunFailure("successful_test_required");
            JsonNode configuration,execution;
            try { configuration=json.readTree(snapshot);execution=configuration.get("execution"); }
            catch(Exception invalid) { throw new RunFailure("deployment_unavailable"); }
            List<UUID> releases=new ArrayList<>();Map<String,String> expected=new HashMap<>();
            JsonNode pinned=configuration.get("skills");
            if(pinned==null||!pinned.isArray()) throw new RunFailure("deployment_unavailable");
            for(var binding:pinned) {
                try {
                    String id=UUID.fromString(binding.path("releaseId").asText()).toString();
                    if(expected.put(id,binding.path("manifestSha256").asText())!=null) throw new IllegalArgumentException();
                    releases.add(UUID.fromString(id));
                } catch(IllegalArgumentException invalid) { throw new RunFailure("deployment_unavailable"); }
            }
            var targetBindings=bindings(scope,releases);
            for(var binding:targetBindings)
                if(!Objects.equals(expected.get(binding.get("release_id")),binding.get("manifest_sha256")))
                    throw new RunFailure("deployment_unavailable");
            String target=insert(scope,(String)previous.get("display_name"),(String)previous.get("agent_version"),
                    input.modelConnectionId().toString(),input.modelVersion(),execution,targetBindings,actor.uid());
            jdbc.update("INSERT INTO oa_deployment_promotion(tenant_id,source_deployment_id,target_deployment_id,verification_run_id,approved_by) VALUES(?,?,?,?,?)",
                    tenant,source,target,input.verificationRunId().toString(),actor.uid());
            audit(scope,actor.uid(),"deployment.published",target);
            return metadata(scope,target);
        });
    }

    public void disable(RunLedger.Scope scope,String id) {
        tx.executeWithoutResult(status->{
            lock(scope); var actor=access.require(scope.tenant(),scope.environment(),scope.environment().equals("prod")
                    ? TenantPermission.DEPLOYMENT_PUBLISH:TenantPermission.RESOURCE_WRITE);
            var deployment=metadata(scope,id);
            if("disabled".equals(deployment.get("state"))) return;
            jdbc.update("UPDATE oa_agent_deployment SET state='disabled' WHERE tenant_id=? AND environment=? AND deployment_id=?",scope.tenant(),scope.environment(),id);
            audit(scope,actor.uid(),"deployment.disabled",id);
        });
    }

    Map<String,Object> metadata(RunLedger.Scope scope,String id) {
        var rows=jdbc.queryForList("SELECT "+COLUMNS+" FROM oa_agent_deployment WHERE tenant_id=? AND environment=? AND deployment_id=?",scope.tenant(),scope.environment(),id);
        if(rows.isEmpty()) throw new RunFailure("resource_unavailable");
        return rows.getFirst();
    }
    void lock(RunLedger.Scope scope) {
        if(jdbc.queryForList("SELECT tenant_id FROM oa_tenant WHERE tenant_id=? FOR UPDATE",scope.tenant()).isEmpty()
                || jdbc.queryForList("SELECT tenant_id FROM oa_tenant_environment WHERE tenant_id=? AND environment=? FOR UPDATE",scope.tenant(),scope.environment()).isEmpty())
            throw new RunFailure("resource_unavailable");
    }
    private List<Map<String,Object>> bindings(RunLedger.Scope scope,List<UUID> ids) {
        if(ids.size()>20 || new HashSet<>(ids).size()!=ids.size()) throw new RunFailure("invalid_skill_bindings");
        List<Map<String,Object>> result=new ArrayList<>();
        Set<String> names=new HashSet<>(),images=new HashSet<>();
        for(UUID id:ids.stream().sorted().toList()) {
            var rows=jdbc.queryForList("""
                    SELECT s.release_id,s.manifest_sha256,s.skill_name,s.runtime_image FROM oa_skill_release s
                    JOIN oa_skill_release_environment g ON g.tenant_id=s.tenant_id AND g.release_id=s.release_id
                    WHERE s.tenant_id=? AND g.environment=? AND s.release_id=? AND s.status='ready'
                    """,scope.tenant(),scope.environment(),id.toString());
            if(rows.isEmpty()) throw new RunFailure("skill_unavailable");
            var row=rows.getFirst();
            if(!names.add((String)row.get("skill_name"))) throw new RunFailure("invalid_skill_bindings");
            images.add((String)row.get("runtime_image")); result.add(row);
        }
        if(images.size()>1) throw new RunFailure("invalid_skill_bindings");
        return result;
    }
    private String insert(RunLedger.Scope scope,String name,String version,String model,long modelVersion,JsonNode execution,List<Map<String,Object>> releases,String actor) {
        var models=jdbc.queryForList("""
                SELECT v.supports_tools FROM oa_model_connection c JOIN oa_model_connection_version v
                ON v.tenant_id=c.tenant_id AND v.environment=c.environment AND v.connection_id=c.connection_id
                WHERE c.tenant_id=? AND c.environment=? AND c.connection_id=? AND v.version=? AND c.enabled=true AND v.revoked=false
                """,scope.tenant(),scope.environment(),model,modelVersion);
        if(models.isEmpty()) throw new RunFailure("model_unavailable");
        if(!releases.isEmpty() && !Boolean.TRUE.equals(models.getFirst().get("supports_tools"))) throw new RunFailure("model_tools_required");
        ObjectNode snapshot=json.createObjectNode();snapshot.put("schemaVersion",1).put("agentVersion",version).put("executorKind","deepagents");
        snapshot.set("execution",execution);
        snapshot.set("skills",json.valueToTree(releases.stream().map(row->Map.of("releaseId",row.get("release_id"),"manifestSha256",row.get("manifest_sha256"))).toList()));
        snapshot.set("model",json.valueToTree(Map.of("connectionId",model,"version",modelVersion)));
        String body=RunJson.encode(json,snapshot,65536),id=UUID.randomUUID().toString();
        jdbc.update("""
                INSERT INTO oa_agent_deployment(tenant_id,environment,deployment_id,display_name,executor_kind,agent_version,
                    model_connection_id,model_version,snapshot_json,snapshot_sha256,state,created_by)
                VALUES(?,?,?,?,'deepagents',?,?,?,?,?,?,?)
                """,scope.tenant(),scope.environment(),id,name,version,model,modelVersion,body,RunJson.hash(body),scope.environment().equals("test")?"test":"published",actor);
        for(var release:releases) {
            jdbc.update("INSERT INTO oa_agent_deployment_skill(tenant_id,environment,deployment_id,release_id,manifest_sha256) VALUES(?,?,?,?,?)",
                    scope.tenant(),scope.environment(),id,release.get("release_id"),release.get("manifest_sha256"));
            jdbc.update("INSERT INTO oa_skill_release_reference(tenant_id,release_id,reference_type,reference_id,retain_until) VALUES(?,?,'deployment',?,NULL)",
                    scope.tenant(),release.get("release_id"),id);
        }
        return id;
    }
    void audit(RunLedger.Scope scope,String actor,String action,String id) {
        jdbc.update("""
                INSERT INTO oa_tenant_audit(tenant_id,environment,actor_uid,action,subject_id,retain_until)
                SELECT tenant_id,environment,?,?,?,TIMESTAMPADD(DAY,audit_retention_days,CURRENT_TIMESTAMP(3))
                FROM oa_tenant_environment WHERE tenant_id=? AND environment=?
                """,actor,action,id,scope.tenant(),scope.environment());
    }
}
