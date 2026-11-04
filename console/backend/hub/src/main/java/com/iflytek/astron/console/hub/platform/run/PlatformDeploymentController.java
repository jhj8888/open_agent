package com.iflytek.astron.console.hub.platform.run;

import com.iflytek.astron.console.commons.response.ApiResult;
import com.iflytek.astron.console.commons.tenant.*;
import jakarta.validation.Valid;
import java.util.UUID;
import org.springframework.http.CacheControl;
import org.springframework.http.ResponseEntity;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.support.TransactionTemplate;
import org.springframework.web.bind.annotation.*;

/** Console management remains on the existing JWT-only chain; service keys cannot manage deployments. */
@RestController
@RequestMapping("/platform/tenants/{tenantId}/environments/{environment}/agent-deployments")
public class PlatformDeploymentController {
    private final AgentDeploymentService deployments;
    private final DeploymentKeys keys;
    private final TenantAccessService access;
    private final TenantAccessRepository memberships;
    private final JdbcTemplate jdbc;
    private final TransactionTemplate tx;
    public PlatformDeploymentController(AgentDeploymentService deployments,DeploymentKeys keys,TenantAccessService access,
            TenantAccessRepository memberships,JdbcTemplate jdbc,PlatformTransactionManager manager) {
        this.deployments=deployments;this.keys=keys;this.access=access;this.memberships=memberships;this.jdbc=jdbc;tx=new TransactionTemplate(manager);tx.setTimeout(15);
    }
    @GetMapping public ApiResult<?> list(@PathVariable String tenantId,@PathVariable String environment) {
        return ApiResult.success(deployments.list(new RunLedger.Scope(tenantId,environment)));
    }
    @PostMapping public ApiResult<?> create(@PathVariable String tenantId,@PathVariable String environment,@RequestBody @Valid AgentDeploymentService.Create input) {
        return ApiResult.success(deployments.create(new RunLedger.Scope(tenantId,environment),input));
    }
    @PostMapping("/promote") public ApiResult<?> promote(@PathVariable String tenantId,@PathVariable String environment,@RequestBody @Valid AgentDeploymentService.Promote input) {
        if(!"prod".equals(environment)) throw new RunFailure("invalid_scope");
        return ApiResult.success(deployments.promote(tenantId,input));
    }
    @PostMapping("/{deploymentId}/disable") public ApiResult<?> disable(@PathVariable String tenantId,@PathVariable String environment,@PathVariable UUID deploymentId) {
        deployments.disable(new RunLedger.Scope(tenantId,environment),deploymentId.toString());return ApiResult.success();
    }
    @GetMapping("/{deploymentId}/credentials") public ApiResult<?> keys(@PathVariable String tenantId,@PathVariable String environment,@PathVariable UUID deploymentId) {
        return ApiResult.success(keys.list(new RunLedger.Scope(tenantId,environment),deploymentId.toString()));
    }
    @PostMapping("/{deploymentId}/credentials") public ResponseEntity<?> issue(@PathVariable String tenantId,@PathVariable String environment,
            @PathVariable UUID deploymentId,@RequestBody @Valid DeploymentKeys.KeyInput input) {
        return secret(keys.issue(new RunLedger.Scope(tenantId,environment),deploymentId.toString(),input));
    }
    @PostMapping("/{deploymentId}/credentials/{keyId}/rotate") public ResponseEntity<?> rotate(@PathVariable String tenantId,@PathVariable String environment,@PathVariable UUID deploymentId,@PathVariable UUID keyId) {
        return secret(keys.rotate(new RunLedger.Scope(tenantId,environment),deploymentId.toString(),keyId.toString()));
    }
    @DeleteMapping("/{deploymentId}/credentials/{keyId}") public ApiResult<?> revoke(@PathVariable String tenantId,@PathVariable String environment,@PathVariable UUID deploymentId,@PathVariable UUID keyId) {
        keys.revoke(new RunLedger.Scope(tenantId,environment),deploymentId.toString(),keyId.toString());return ApiResult.success();
    }
    @GetMapping("/{deploymentId}/grants") public ApiResult<?> grants(@PathVariable String tenantId,@PathVariable String environment,@PathVariable UUID deploymentId) {
        var scope=new RunLedger.Scope(tenantId,environment);access.require(tenantId,environment,TenantPermission.CREDENTIAL_MANAGE);
        deployments.metadata(scope,deploymentId.toString());
        return ApiResult.success(jdbc.queryForList("SELECT uid,granted_by,created_at FROM oa_deployment_grant WHERE tenant_id=? AND environment=? AND deployment_id=? ORDER BY uid LIMIT 200",tenantId,environment,deploymentId.toString()));
    }
    @PutMapping("/{deploymentId}/grants/{uid}") public ApiResult<?> grant(@PathVariable String tenantId,@PathVariable String environment,@PathVariable UUID deploymentId,@PathVariable String uid) {
        changeGrant(new RunLedger.Scope(tenantId,environment),deploymentId.toString(),uid,true);return ApiResult.success();
    }
    @DeleteMapping("/{deploymentId}/grants/{uid}") public ApiResult<?> revokeGrant(@PathVariable String tenantId,@PathVariable String environment,@PathVariable UUID deploymentId,@PathVariable String uid) {
        changeGrant(new RunLedger.Scope(tenantId,environment),deploymentId.toString(),uid,false);return ApiResult.success();
    }
    private void changeGrant(RunLedger.Scope scope,String deployment,String uid,boolean granted) {
        if(!scope.environment().equals("prod")||uid.isBlank()||uid.length()>128) throw new RunFailure("invalid_grant");
        tx.executeWithoutResult(status->{
            deployments.lock(scope);var actor=access.require(scope.tenant(),scope.environment(),TenantPermission.CREDENTIAL_MANAGE);
            deployments.metadata(scope,deployment);
            if(granted) {
                memberships.findContext(uid,scope.tenant(),TenantEnvironment.PROD).orElseThrow(()->new RunFailure("resource_unavailable"));
                jdbc.update("INSERT INTO oa_deployment_grant(tenant_id,environment,deployment_id,uid,granted_by) VALUES(?,?,?,?,?) ON DUPLICATE KEY UPDATE uid=VALUES(uid)",
                        scope.tenant(),scope.environment(),deployment,uid,actor.uid());
            } else jdbc.update("DELETE FROM oa_deployment_grant WHERE tenant_id=? AND environment=? AND deployment_id=? AND uid=?",scope.tenant(),scope.environment(),deployment,uid);
            deployments.audit(scope,actor.uid(),granted?"deployment.grant.added":"deployment.grant.revoked",deployment);
        });
    }
    private ResponseEntity<?> secret(DeploymentKeys.Issued issued) {
        return ResponseEntity.ok().cacheControl(CacheControl.noStore()).body(ApiResult.success(issued));
    }
}
