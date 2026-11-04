package com.iflytek.astron.console.hub.platform.run;

import com.iflytek.astron.console.commons.tenant.*;
import jakarta.validation.constraints.*;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.util.*;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.support.TransactionTemplate;

/** Random 256-bit opaque credentials; plaintext is returned only once and never persisted. */
@Service
public class DeploymentKeys {
    public static final String PREFIX="oa_depl_";
    private final JdbcTemplate jdbc;
    private final TenantAccessService access;
    private final AgentDeploymentService deployments;
    private final TransactionTemplate tx;
    private final SecureRandom random=new SecureRandom();
    public DeploymentKeys(JdbcTemplate jdbc,TenantAccessService access,AgentDeploymentService deployments,PlatformTransactionManager manager) {
        this.jdbc=jdbc;this.access=access;this.deployments=deployments;tx=new TransactionTemplate(manager);tx.setTimeout(15);
    }
    public record Principal(String keyId,String subjectId) {}
    public record KeyInput(@NotBlank @Size(max=128) String displayName,boolean canCreate,boolean canRead,boolean canCancel,
            @Min(1) @Max(365) int validDays) {}
    public record Issued(String keyId,String token) {
        @Override public String toString() { return "Issued[token=REDACTED]"; }
    }
    public Optional<Principal> authenticate(String token) {
        if(token==null || !token.matches("oa_depl_[a-f0-9-]{36}\\.[A-Za-z0-9_-]{43}")) return Optional.empty();
        String id=token.substring(PREFIX.length(),PREFIX.length()+36);
        try { if(!UUID.fromString(id).toString().equals(id)) return Optional.empty(); }
        catch(RuntimeException invalid) { return Optional.empty(); }
        var rows=jdbc.queryForList("SELECT subject_id,secret_sha256 FROM oa_deployment_credential WHERE key_id=? AND revoked=false AND expires_at>CURRENT_TIMESTAMP(3)",id);
        String expected=rows.isEmpty()?"0".repeat(64):(String)rows.getFirst().get("secret_sha256");
        boolean matched=MessageDigest.isEqual(expected.getBytes(StandardCharsets.US_ASCII),RunJson.hash(token).getBytes(StandardCharsets.US_ASCII));
        if(!matched || rows.isEmpty()) return Optional.empty();
        return Optional.of(new Principal(id,(String)rows.getFirst().get("subject_id")));
    }
    public List<Map<String,Object>> list(RunLedger.Scope scope,String deployment) {
        access.require(scope.tenant(),scope.environment(),TenantPermission.CREDENTIAL_MANAGE);deployments.metadata(scope,deployment);
        return jdbc.queryForList("SELECT key_id,subject_id,display_name,can_create,can_read,can_cancel,revoked,created_at,expires_at,revoked_at FROM oa_deployment_credential "
                +"WHERE tenant_id=? AND environment=? AND deployment_id=? ORDER BY created_at DESC LIMIT 100",scope.tenant(),scope.environment(),deployment);
    }
    public Issued issue(RunLedger.Scope scope,String deployment,KeyInput input) {
        return tx.execute(status->{
            deployments.lock(scope);var actor=access.require(scope.tenant(),scope.environment(),TenantPermission.CREDENTIAL_MANAGE);
            deployments.metadata(scope,deployment);
            var issued=insert(scope,deployment,UUID.randomUUID().toString(),input,actor.uid());
            deployments.audit(scope,actor.uid(),"deployment.key.issued",issued.keyId());return issued;
        });
    }
    public Issued rotate(RunLedger.Scope scope,String deployment,String keyId) {
        return tx.execute(status->{
            deployments.lock(scope);var actor=access.require(scope.tenant(),scope.environment(),TenantPermission.CREDENTIAL_MANAGE);
            var old=key(scope,deployment,keyId);
            if(Boolean.TRUE.equals(old.get("revoked"))) throw new RunFailure("credential_revoked");
            if(jdbc.queryForObject("SELECT expires_at>CURRENT_TIMESTAMP(3) FROM oa_deployment_credential WHERE key_id=?",Boolean.class,keyId)!=Boolean.TRUE)
                throw new RunFailure("credential_expired");
            revokeRow(keyId); // Rolled back with issuance on any failure; rotation also works at the key limit.
            // Keep the subject and exact expiry so rotation neither loses prior runs nor extends access.
            var issued=insert(scope,deployment,(String)old.get("subject_id"),new KeyInput((String)old.get("display_name"),
                    Boolean.TRUE.equals(old.get("can_create")),Boolean.TRUE.equals(old.get("can_read")),Boolean.TRUE.equals(old.get("can_cancel")),1),actor.uid());
            jdbc.update("UPDATE oa_deployment_credential SET expires_at=? WHERE key_id=?",old.get("expires_at"),issued.keyId());
            deployments.audit(scope,actor.uid(),"deployment.key.rotated",keyId);return issued;
        });
    }
    public void revoke(RunLedger.Scope scope,String deployment,String keyId) {
        tx.executeWithoutResult(status->{
            deployments.lock(scope);var actor=access.require(scope.tenant(),scope.environment(),TenantPermission.CREDENTIAL_MANAGE);
            var old=key(scope,deployment,keyId);
            if(Boolean.TRUE.equals(old.get("revoked"))) return;
            revokeRow(keyId);deployments.audit(scope,actor.uid(),"deployment.key.revoked",keyId);
        });
    }
    private Issued insert(RunLedger.Scope scope,String deployment,String subject,KeyInput input,String actor) {
        if(!input.canCreate()&&!input.canRead()&&!input.canCancel()) throw new RunFailure("invalid_credential_scope");
        if(input.validDays()<1||input.validDays()>365) throw new RunFailure("invalid_credential_expiry");
        // Bound outstanding credentials per deployment; old revoked rows remain auditable.
        if(jdbc.queryForObject("SELECT COUNT(*) FROM oa_deployment_credential WHERE tenant_id=? AND environment=? AND deployment_id=? AND revoked=false AND expires_at>CURRENT_TIMESTAMP(3)",
                Long.class,scope.tenant(),scope.environment(),deployment)>=50) throw new RunFailure("credential_limit");
        byte[] secret=new byte[32];random.nextBytes(secret);String id=UUID.randomUUID().toString();
        String token=PREFIX+id+"."+Base64.getUrlEncoder().withoutPadding().encodeToString(secret);
        jdbc.update("""
                INSERT INTO oa_deployment_credential(key_id,subject_id,tenant_id,environment,deployment_id,display_name,secret_sha256,
                    can_create,can_read,can_cancel,created_by,expires_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,TIMESTAMPADD(DAY,?,CURRENT_TIMESTAMP(3)))
                """,id,subject,scope.tenant(),scope.environment(),deployment,input.displayName(),RunJson.hash(token),
                input.canCreate(),input.canRead(),input.canCancel(),actor,input.validDays());
        return new Issued(id,token);
    }
    private Map<String,Object> key(RunLedger.Scope scope,String deployment,String id) {
        var rows=jdbc.queryForList("SELECT * FROM oa_deployment_credential WHERE tenant_id=? AND environment=? AND deployment_id=? AND key_id=? FOR UPDATE",
                scope.tenant(),scope.environment(),deployment,id);
        if(rows.isEmpty()) throw new RunFailure("resource_unavailable");return rows.getFirst();
    }
    private void revokeRow(String id) { jdbc.update("UPDATE oa_deployment_credential SET revoked=true,revoked_at=CURRENT_TIMESTAMP(3) WHERE key_id=?",id); }
}
