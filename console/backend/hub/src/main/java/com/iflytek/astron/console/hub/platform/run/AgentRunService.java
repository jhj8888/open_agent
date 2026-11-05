package com.iflytek.astron.console.hub.platform.run;

import com.fasterxml.jackson.annotation.JsonAnySetter;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.validation.Valid;
import jakarta.validation.constraints.*;
import java.util.*;
import org.springframework.core.env.Environment;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.support.TransactionTemplate;
import static com.iflytek.astron.console.hub.platform.run.DeploymentAuthorization.Action.*;

/** One admission/query path for browser debugging and externally authenticated callers. */
@Service
public class AgentRunService {
    private final RunLedger ledger;
    private final DeploymentAuthorization authorization;
    private final AgentDeploymentService deployments;
    private final JdbcTemplate jdbc;
    private final ObjectMapper json;
    private final Environment environment;
    private final TransactionTemplate tx;
    public AgentRunService(RunLedger ledger,DeploymentAuthorization authorization,AgentDeploymentService deployments,
            JdbcTemplate jdbc,ObjectMapper json,Environment environment,PlatformTransactionManager manager) {
        this.ledger=ledger;this.authorization=authorization;this.deployments=deployments;this.jdbc=jdbc;this.json=json;this.environment=environment;
        tx=new TransactionTemplate(manager);tx.setTimeout(15);
    }
    public record Message(@NotBlank @Pattern(regexp="user|assistant") String role,@NotBlank @Size(max=8192) String content) {
        @JsonAnySetter public void unknown(String name,JsonNode value) { throw new RunFailure("invalid_input"); }
        @Override public String toString() { return "Message[content=REDACTED]"; }
    }
    public record Input(@NotBlank @Size(max=32768) String message,@Size(max=32) List<@NotNull @Valid Message> history) {
        public Input { if(history==null) history=List.of(); }
        @JsonAnySetter public void unknown(String name,JsonNode value) { throw new RunFailure("invalid_input"); }
        @Override public String toString() { return "Input[content=REDACTED]"; }
    }
    public record Submit(@NotNull @Valid Input input,@Min(1) @Max(3600) Integer timeoutSeconds) {
        public Submit { if(timeoutSeconds==null) timeoutSeconds=300; }
        @JsonAnySetter public void unknown(String name,JsonNode value) { throw new RunFailure("invalid_input"); }
        @Override public String toString() { return "Submit[input=REDACTED]"; }
    }
    public record Accepted(String run_id,String status_url,String events_url,boolean reused) {}
    public record Events(List<Map<String,Object>> events,long last_sequence,boolean terminal) {}

    public Accepted submit(DeploymentAuthorization.Caller caller,String id,String key,Submit input) {
        var deployment=authorization.resolve(id);authorization.require(caller,deployment,CREATE,null);
        if(!admissionEnabled(deployment.scope().environment())) throw new RunFailure("executor_unavailable");
        if(!"deepagents".equals(deployment.metadata().get("executor_kind"))) throw new RunFailure("executor_unavailable");
        var accepted=ledger.submit(deployment.scope(),id,caller.actor(),key,json.valueToTree(input.input()),input.timeoutSeconds(),
                ()->authorization.require(caller,deployment,CREATE,null));
        return new Accepted(accepted.runId(),"/api/v1/runs/"+accepted.runId(),"/api/v1/runs/"+accepted.runId()+"/events",accepted.reused());
    }
    public Map<String,Object> deployment(DeploymentAuthorization.Caller caller,String id) {
        var deployment=authorization.resolve(id);
        return tx.execute(status->{deployments.lock(deployment.scope());authorization.require(caller,deployment,METADATA,null);
            var result=new LinkedHashMap<>(deployments.metadata(deployment.scope(),id));
            result.put("admission_enabled",admissionEnabled(deployment.scope().environment()));
            result.put("allowed_actions",Map.of("create",allows(caller,deployment,CREATE),
                    "read",allows(caller,deployment,READ),"cancel",allows(caller,deployment,CANCEL)));
            return result;});
    }
    private boolean admissionEnabled(String target) {
        if(!environment.getProperty("PLATFORM_RUN_ADMISSION_ENABLED",Boolean.class,false)) return false;
        String configured=environment.getProperty("PLATFORM_RUN_ADMISSION_ENVIRONMENTS","test");
        // Omitted legacy configuration enables test only. Malformed values fail closed.
        return Set.of("test","prod","test,prod","prod,test").contains(configured)
                && Arrays.asList(configured.split(",")).contains(target);
    }
    private boolean allows(DeploymentAuthorization.Caller caller,DeploymentAuthorization.Deployment deployment,DeploymentAuthorization.Action action) {
        try { authorization.require(caller,deployment,action,null);return true; }
        catch(RunFailure denied) {
            if(Set.of("action_forbidden","resource_unavailable").contains(denied.code())) return false;
            throw denied;
        }
    }
    public List<Map<String,Object>> list(DeploymentAuthorization.Caller caller,String id) {
        var deployment=authorization.resolve(id);
        return tx.execute(status->{
            deployments.lock(deployment.scope());authorization.require(caller,deployment,READ,null);
            boolean own=authorization.onlyOwnRuns(caller,deployment);
            List<Object> args=new ArrayList<>(List.of(deployment.scope().tenant(),deployment.scope().environment(),id));
            if(own) args.add(caller.actor());
            var rows=jdbc.queryForList("SELECT run_id,state,trace_id,error_code,usage_json,created_at,started_at,finished_at,deadline_at FROM oa_agent_run "
                    +"WHERE tenant_id=? AND environment=? AND deployment_id=?"+(own?" AND actor_id=?":"")+" ORDER BY created_at DESC,run_id LIMIT 50",args.toArray());
            rows.forEach(row -> row.put("usage",parse(row.remove("usage_json"))));
            return rows;
        });
    }
    public Map<String,Object> status(DeploymentAuthorization.Caller caller,String runId) {
        var deployment=authorization.runDeployment(runId);
        return tx.execute(status->{
            deployments.lock(deployment.scope());authorization.require(caller,deployment,READ,runId);
            return describe(deployment,ledger.get(deployment.scope(),runId));
        });
    }
    public Map<String,Object> cancel(DeploymentAuthorization.Caller caller,String runId) {
        var deployment=authorization.runDeployment(runId);
        var result=ledger.cancel(deployment.scope(),runId,caller.actor(),()->authorization.require(caller,deployment,CANCEL,runId));
        return describe(deployment,result);
    }
    public Events events(DeploymentAuthorization.Caller caller,String runId,long after) {
        var deployment=authorization.runDeployment(runId);
        var page=ledger.events(deployment.scope(),runId,after,100,()->authorization.require(caller,deployment,READ,runId));
        var events=page.events().stream().map(event->{
            var value=new LinkedHashMap<>(event);value.put("run_id",runId);value.put("payload",parse(value.remove("payload_json")));return (Map<String,Object>)value;
        }).toList();
        return new Events(events,page.lastSequence(),page.terminal());
    }
    private Map<String,Object> describe(DeploymentAuthorization.Deployment deployment,Map<String,Object> row) {
        var result=new LinkedHashMap<>(row);result.put("environment",deployment.scope().environment());
        result.put("agent_version",deployment.metadata().get("agent_version"));result.put("executor_kind",deployment.metadata().get("executor_kind"));
        result.put("usage",parse(result.remove("usage_json")));return result;
    }
    private JsonNode parse(Object value) {
        if(value==null) return null;
        try { return json.readTree(value.toString()); }
        catch(Exception invalid) { throw new RunFailure("stored_event_unavailable"); }
    }
}
