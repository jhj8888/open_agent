package com.iflytek.astron.console.hub.platform.run;

import com.iflytek.astron.console.commons.tenant.*;
import java.util.Map;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.stereotype.Component;

/** Immutable verified identity may cross async boundaries; permissions never come from a stale cache. */
@Component
public class DeploymentAuthorization {
    public enum Action { METADATA, CREATE, READ, CANCEL, DELETE }
    public record Caller(String uid,DeploymentKeys.Principal service) {
        public Caller {
            if((uid==null)==(service==null) || (uid!=null&&(uid.isBlank()||uid.length()>128))) throw new RunFailure("unauthenticated");
        }
        public String actor() { return service==null?"user:"+RunJson.hash(uid):"service:"+service.subjectId(); }
    }
    public record Deployment(RunLedger.Scope scope,String id,Map<String,Object> metadata) {}
    private final JdbcTemplate jdbc;
    private final TenantAccessRepository memberships;
    public DeploymentAuthorization(JdbcTemplate jdbc,TenantAccessRepository memberships) { this.jdbc=jdbc;this.memberships=memberships; }
    public static Caller current() {
        var auth=SecurityContextHolder.getContext().getAuthentication();
        if(auth!=null&&auth.isAuthenticated()) {
            if(auth.getPrincipal() instanceof DeploymentKeys.Principal key) return new Caller(null,key);
            if(auth.getPrincipal() instanceof Jwt jwt) return new Caller(jwt.getSubject(),null);
        }
        throw new RunFailure("unauthenticated");
    }
    public Deployment resolve(String id) {
        var rows=jdbc.queryForList("SELECT "+AgentDeploymentService.COLUMNS+" FROM oa_agent_deployment WHERE deployment_id=?",id);
        if(rows.isEmpty()) throw new RunFailure("resource_unavailable");
        var row=rows.getFirst();return new Deployment(new RunLedger.Scope((String)row.get("tenant_id"),(String)row.get("environment")),id,row);
    }
    public Deployment runDeployment(String runId) {
        var rows=jdbc.queryForList("SELECT deployment_id FROM oa_agent_run WHERE run_id=?",runId);
        if(rows.isEmpty()) throw new RunFailure("resource_unavailable");return resolve((String)rows.getFirst().get("deployment_id"));
    }
    public void require(Caller caller,Deployment deployment,Action action,String runId) {
        var scope=deployment.scope();
        if(runId!=null && action!=Action.DELETE && !jdbc.queryForList(
                "SELECT run_id FROM oa_run_deletion WHERE run_id=?",runId).isEmpty())throw new RunFailure("resource_unavailable");
        if(caller.service()!=null) {
            if(action==Action.DELETE)throw new RunFailure("action_forbidden");
            var rows=jdbc.queryForList("""
                    SELECT k.can_create,k.can_read,k.can_cancel FROM oa_deployment_credential k
                    JOIN oa_tenant t ON t.tenant_id=k.tenant_id
                    WHERE k.key_id=? AND k.subject_id=? AND k.tenant_id=? AND k.environment=? AND k.deployment_id=?
                        AND k.revoked=false AND k.expires_at>CURRENT_TIMESTAMP(3) AND t.status='active'
                    """,caller.service().keyId(),caller.service().subjectId(),scope.tenant(),scope.environment(),deployment.id());
            if(rows.isEmpty()) throw new RunFailure("resource_unavailable");
            if(action!=Action.METADATA&&!Boolean.TRUE.equals(rows.getFirst().get("can_"+action.name().toLowerCase()))) throw new RunFailure("action_forbidden");
            if(runId!=null) own(caller,deployment,runId);
            return;
        }
        var context=memberships.findContext(caller.uid(),scope.tenant(),TenantEnvironment.parse(scope.environment())).orElseThrow(()->new RunFailure("resource_unavailable"));
        if(!TenantAccessPolicy.allows(context.role(),context.environment(),context.status(),TenantPermission.METADATA_READ)) throw new RunFailure("resource_unavailable");
        if(action==Action.DELETE) {
            if(context.role()!=TenantRole.TENANT_ADMIN || !"active".equals(context.status()))throw new RunFailure("action_forbidden");
            return;
        }
        if(action==Action.METADATA) return;
        var permission=switch(action) { case CREATE->TenantPermission.RUN_CREATE;case READ->TenantPermission.LOG_READ;case CANCEL->TenantPermission.RUN_CANCEL;default->throw new IllegalStateException(); };
        if(TenantAccessPolicy.allows(context.role(),context.environment(),context.status(),permission)) return;
        // Production role alone never grants execution. Delegates can read/cancel only their own runs.
        if(scope.environment().equals("prod")&&context.status().equals("active")&&!jdbc.queryForList(
                "SELECT uid FROM oa_deployment_grant WHERE tenant_id=? AND environment=? AND deployment_id=? AND uid=?",
                scope.tenant(),scope.environment(),deployment.id(),caller.uid()).isEmpty()) {
            if(runId!=null) own(caller,deployment,runId);return;
        }
        throw new RunFailure("action_forbidden");
    }
    private void own(Caller caller,Deployment deployment,String runId) {
        if(jdbc.queryForList("SELECT run_id FROM oa_agent_run WHERE tenant_id=? AND environment=? AND deployment_id=? AND run_id=? AND actor_id=?",
                deployment.scope().tenant(),deployment.scope().environment(),deployment.id(),runId,caller.actor()).isEmpty()) throw new RunFailure("resource_unavailable");
    }
    public boolean onlyOwnRuns(Caller caller,Deployment deployment) {
        if(caller.service()!=null) return true;
        var scope=deployment.scope();
        var context=memberships.findContext(caller.uid(),scope.tenant(),TenantEnvironment.parse(scope.environment())).orElseThrow(()->new RunFailure("resource_unavailable"));
        return !TenantAccessPolicy.allows(context.role(),context.environment(),context.status(),TenantPermission.LOG_READ);
    }
}
