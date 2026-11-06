package com.iflytek.astron.console.hub.platform.run;

import com.iflytek.astron.console.hub.platform.model.ModelConnectionService;
import java.util.Map;
import java.util.UUID;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;

/** Worker-only model request accounting; scope and model bounds never come from user input. */
@Service
public class RunModelBudgetService {
    private final RunLedger runs;
    private final TokenBudgetLedger budgets;
    private final ModelConnectionService models;
    private final JdbcTemplate jdbc;
    public RunModelBudgetService(RunLedger runs,TokenBudgetLedger budgets,ModelConnectionService models,JdbcTemplate jdbc) {
        this.runs=runs;this.budgets=budgets;this.models=models;this.jdbc=jdbc;
    }

    public TokenBudgetLedger.Reservation prepare(RunLedger.Lease lease,String callId,String requestSha256) {
        identity(callId,requestSha256);
        return runs.withPermit(lease,()->{
            var scope=lease.scope();var old=find(lease,callId,requestSha256,false);
            var deployment=jdbc.queryForMap("""
                    SELECT d.model_connection_id,d.model_version FROM oa_agent_run r JOIN oa_agent_deployment d
                      ON d.tenant_id=r.tenant_id AND d.environment=r.environment AND d.deployment_id=r.deployment_id
                    WHERE r.tenant_id=? AND r.environment=? AND r.run_id=?
                    """,scope.tenant(),scope.environment(),lease.runId());
            String connection=(String)deployment.get("model_connection_id");long version=((Number)deployment.get("model_version")).longValue();
            var bound=bound(lease,connection,version);
            String fingerprint=RunJson.hash(String.join("\0","run-model-call-v1",scope.tenant(),scope.environment(),lease.runId(),
                    Integer.toString(lease.attempt()),lease.leaseId(),Long.toString(lease.fencingToken()),lease.workerId(),callId,
                    requestSha256,connection,Long.toString(version),bound.contractSha256()));
            if(old!=null && !fingerprint.equals(old.get("ledger_fingerprint")))throw new RunFailure("token_request_conflict");
            // Outer withPermit holds tenant/environment and exact run/attempt locks through commit.
            var receipt=budgets.prepare(scope,callId,fingerprint,bound.maxTotalTokens(),()->{});
            if(old==null)jdbc.update("""
                    INSERT INTO oa_run_model_token_call(tenant_id,environment,call_id,run_id,attempt_no,lease_id,
                      request_sha256,model_connection_id,model_version,contract_sha256,ledger_fingerprint) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                    """,scope.tenant(),scope.environment(),callId,lease.runId(),lease.attempt(),lease.leaseId(),requestSha256,
                    connection,version,bound.contractSha256(),fingerprint);
            return receipt;
        });
    }

    public boolean dispatch(RunLedger.Lease lease,String callId,String requestSha256) {
        identity(callId,requestSha256);
        return runs.withPermit(lease,()->{
            var row=find(lease,callId,requestSha256,true);var scope=lease.scope();
            var bound=bound(lease,(String)row.get("model_connection_id"),
                    ((Number)row.get("model_version")).longValue());
            if(!bound.contractSha256().equals(row.get("contract_sha256")))throw new RunFailure("token_request_conflict");
            return budgets.dispatch(scope,callId,(String)row.get("ledger_fingerprint"),()->{});
        });
    }

    public boolean cancelPrepared(RunLedger.Lease lease,String callId,String requestSha256) {
        identity(callId,requestSha256);
        return runs.withAttempt(lease,()->budgets.cancelPrepared(lease.scope(),callId,
                (String)find(lease,callId,requestSha256,true).get("ledger_fingerprint"),()->{}));
    }
    public TokenBudgetLedger.Reservation unknown(RunLedger.Lease lease,String callId,String requestSha256) {
        identity(callId,requestSha256);
        return runs.withAttempt(lease,()->budgets.unknown(lease.scope(),callId,
                (String)find(lease,callId,requestSha256,true).get("ledger_fingerprint"),()->{}));
    }
    public TokenBudgetLedger.Reservation settle(RunLedger.Lease lease,String callId,String requestSha256,long input,long output) {
        identity(callId,requestSha256);
        return runs.withAttempt(lease,()->budgets.settle(lease.scope(),callId,
                (String)find(lease,callId,requestSha256,true).get("ledger_fingerprint"),input,output,()->{}));
    }

    private Map<String,Object> find(RunLedger.Lease lease,String id,String requestSha,boolean required) {
        var rows=jdbc.queryForList("SELECT * FROM oa_run_model_token_call WHERE tenant_id=? AND environment=? AND call_id=? FOR UPDATE",
                lease.scope().tenant(),lease.scope().environment(),id);
        if(rows.isEmpty()) {if(required)throw new RunFailure("token_reservation_missing");return null;}
        var row=rows.getFirst();
        if(!lease.runId().equals(row.get("run_id")) || lease.attempt()!=((Number)row.get("attempt_no")).intValue()
                || !lease.leaseId().equals(row.get("lease_id")) || !requestSha.equals(row.get("request_sha256")))
            throw new RunFailure("token_request_conflict");
        return row;
    }
    private com.iflytek.astron.console.hub.platform.model.ModelTokenBoundPolicy.Bound bound(RunLedger.Lease lease,String connection,long version) {
        try {return models.tokenBoundForAuthorizedVersion(lease.scope().tenant(),lease.scope().environment(),connection,version);}
        catch(IllegalStateException unavailable) {throw new RunFailure("token_bound_unavailable");}
    }
    private static void identity(String callId,String requestSha256) {
        try {if(callId==null || !UUID.fromString(callId).toString().equals(callId))throw new IllegalArgumentException();}
        catch(IllegalArgumentException invalid){throw new RunFailure("invalid_token_call");}
        if(requestSha256==null || !requestSha256.matches("[a-f0-9]{64}"))throw new RunFailure("invalid_token_call");
    }
}
