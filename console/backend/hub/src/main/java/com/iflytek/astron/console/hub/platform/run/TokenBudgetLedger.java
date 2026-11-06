package com.iflytek.astron.console.hub.platform.run;

import com.iflytek.astron.console.hub.platform.AgentPeriodStatistics;
import java.math.BigDecimal;
import java.sql.Date;
import java.sql.Timestamp;
import java.time.Clock;
import java.time.LocalDate;
import java.util.Map;
import java.util.UUID;
import java.util.function.Supplier;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.TransactionDefinition;
import org.springframework.transaction.support.TransactionTemplate;

/** Internal ledger, NOT an HTTP authorization boundary or a model request executor.
 * Callers supply a trusted total-token bound and reauthorize under the scope lock.
 * Lock order: tenant -> environment -> reservation/day. No network I/O in callbacks.
 * A repeated prepare is a receipt only; only the first successful dispatch grants a call.
 */
@Component
public class TokenBudgetLedger {
    private final JdbcTemplate jdbc;
    private final TransactionTemplate transaction;
    private final Clock clock;

    @Autowired public TokenBudgetLedger(JdbcTemplate jdbc,PlatformTransactionManager manager) {
        this(jdbc,manager,null);
    }
    public TokenBudgetLedger(JdbcTemplate jdbc,PlatformTransactionManager manager,Clock clock) {
        this.jdbc=jdbc;this.clock=clock;transaction=new TransactionTemplate(manager);
        transaction.setIsolationLevel(TransactionDefinition.ISOLATION_READ_COMMITTED);
        transaction.setTimeout(15);
    }
    public record Reservation(String callId,String fingerprint,LocalDate day,long boundTokens,String phase,
                              Long inputTokens,Long outputTokens) {}
    public record DayUsage(LocalDate day,BigDecimal heldTokens,BigDecimal spentTokens,boolean blockedByBreach) {
        public BigDecimal occupiedTokens() {return heldTokens.add(spentTokens);}
    }

    public Reservation prepare(RunLedger.Scope scope,String callId,String fingerprint,long bound,Runnable authorize) {
        identity(callId,fingerprint);
        if(bound<1 || bound>1_000_000_000L)throw new RunFailure("invalid_token_bound");
        return tx(()->{
            long limit=lockScope(scope);authorize.run();
            var old=find(scope,callId);
            if(old!=null) {
                match(old,fingerprint);
                if(old.boundTokens()!=bound)throw new RunFailure("token_request_conflict");
                return old;
            }
            if(breached(scope))throw new RunFailure("token_bound_breached");
            var now=now();var day=now.atZone(AgentPeriodStatistics.ZONE).toLocalDate();
            jdbc.update("INSERT IGNORE INTO oa_model_token_day(tenant_id,environment,budget_day) VALUES(?,?,?)",
                    scope.tenant(),scope.environment(),Date.valueOf(day));
            var usage=day(scope,day,false);
            if(usage.occupiedTokens().add(BigDecimal.valueOf(bound)).compareTo(BigDecimal.valueOf(limit))>0)
                throw new RunFailure("token_budget_exhausted");
            jdbc.update("""
                    INSERT INTO oa_model_token_reservation(tenant_id,environment,call_id,request_sha256,budget_day,
                      bound_tokens,phase,prepared_at) VALUES(?,?,?,?,?,?,'prepared',?)
                    """,scope.tenant(),scope.environment(),callId,fingerprint,Date.valueOf(day),bound,Timestamp.from(now));
            add(scope,day,BigDecimal.valueOf(bound),BigDecimal.ZERO);
            return find(scope,callId);
        });
    }

    /** False means no permission, including a lost response to an earlier successful dispatch. */
    public boolean dispatch(RunLedger.Scope scope,String callId,String fingerprint,Runnable authorize) {
        identity(callId,fingerprint);
        return tx(()->{
            long limit=lockScope(scope);authorize.run();var reservation=required(scope,callId,fingerprint);
            if(!reservation.phase().equals("prepared"))return false;
            if(breached(scope))throw new RunFailure("token_bound_breached");
            var now=now();
            if(!reservation.day().equals(now.atZone(AgentPeriodStatistics.ZONE).toLocalDate()))
                throw new RunFailure("token_reservation_expired");
            if(day(scope,reservation.day(),false).occupiedTokens().compareTo(BigDecimal.valueOf(limit))>0)
                throw new RunFailure("token_budget_exhausted");
            return jdbc.update("""
                    UPDATE oa_model_token_reservation SET phase='dispatched',dispatched_at=?
                    WHERE tenant_id=? AND environment=? AND call_id=? AND phase='prepared'
                    """,Timestamp.from(now),scope.tenant(),scope.environment(),callId)==1;
        });
    }

    public boolean cancelPrepared(RunLedger.Scope scope,String callId,String fingerprint,Runnable authorize) {
        identity(callId,fingerprint);
        return tx(()->{
            lockScope(scope);authorize.run();var reservation=required(scope,callId,fingerprint);
            if(reservation.phase().equals("cancelled"))return true;
            if(!reservation.phase().equals("prepared"))return false;
            jdbc.update("UPDATE oa_model_token_reservation SET phase='cancelled' WHERE tenant_id=? AND environment=? AND call_id=?",
                    scope.tenant(),scope.environment(),callId);
            add(scope,reservation.day(),BigDecimal.valueOf(-reservation.boundTokens()),BigDecimal.ZERO);
            return true;
        });
    }

    public Reservation unknown(RunLedger.Scope scope,String callId,String fingerprint,Runnable authorize) {
        identity(callId,fingerprint);
        return tx(()->{
            lockScope(scope);authorize.run();var reservation=required(scope,callId,fingerprint);
            if(reservation.phase().equals("prepared") || reservation.phase().equals("cancelled"))
                throw new RunFailure("token_call_not_dispatched");
            if(reservation.phase().equals("dispatched"))jdbc.update(
                    "UPDATE oa_model_token_reservation SET phase='unknown' WHERE tenant_id=? AND environment=? AND call_id=?",
                    scope.tenant(),scope.environment(),callId);
            return find(scope,callId); // A late unknown notification never erases known usage.
        });
    }

    public Reservation settle(RunLedger.Scope scope,String callId,String fingerprint,long input,long output,Runnable authorize) {
        identity(callId,fingerprint);
        if(input<0 || output<0 || input>1_000_000_000L || output>1_000_000_000L)throw new RunFailure("invalid_token_usage");
        return tx(()->{
            lockScope(scope);authorize.run();var reservation=required(scope,callId,fingerprint);
            if(reservation.phase().equals("settled") || reservation.phase().equals("breached")) {
                if(reservation.inputTokens()!=input || reservation.outputTokens()!=output)throw new RunFailure("token_usage_conflict");
                return reservation;
            }
            if(!reservation.phase().equals("dispatched") && !reservation.phase().equals("unknown"))
                throw new RunFailure("token_call_not_dispatched");
            // Record over-bound usage without throwing/rolling back the actual consumption.
            jdbc.update("""
                    UPDATE oa_model_token_reservation SET phase=?,input_tokens=?,output_tokens=?,settled_at=?
                    WHERE tenant_id=? AND environment=? AND call_id=?
                    """,input+output>reservation.boundTokens()?"breached":"settled",input,output,Timestamp.from(now()),
                    scope.tenant(),scope.environment(),callId);
            add(scope,reservation.day(),BigDecimal.valueOf(-reservation.boundTokens()),BigDecimal.valueOf(input+output));
            return find(scope,callId);
        });
    }

    public DayUsage usage(RunLedger.Scope scope,LocalDate date,Runnable authorize) {
        if(date==null)throw new RunFailure("invalid_budget_day");
        return tx(()->{lockScope(scope);authorize.run();return day(scope,date,breached(scope));});
    }
    private long lockScope(RunLedger.Scope scope) {
        if(jdbc.queryForList("SELECT tenant_id FROM oa_tenant WHERE tenant_id=? FOR UPDATE",scope.tenant()).isEmpty())
            throw new RunFailure("resource_unavailable");
        var rows=jdbc.queryForList("SELECT daily_token_budget FROM oa_tenant_environment WHERE tenant_id=? AND environment=? FOR UPDATE",
                scope.tenant(),scope.environment());
        if(rows.isEmpty())throw new RunFailure("resource_unavailable");
        return ((Number)rows.getFirst().get("daily_token_budget")).longValue();
    }
    private boolean breached(RunLedger.Scope scope) {
        return !jdbc.queryForList("""
                SELECT call_id FROM oa_model_token_reservation WHERE tenant_id=? AND environment=? AND phase='breached' LIMIT 1 FOR UPDATE
                """,scope.tenant(),scope.environment()).isEmpty();
    }
    private DayUsage day(RunLedger.Scope scope,LocalDate date,boolean breached) {
        var rows=jdbc.queryForList("SELECT held_tokens,spent_tokens FROM oa_model_token_day WHERE tenant_id=? AND environment=? AND budget_day=? FOR UPDATE",
                scope.tenant(),scope.environment(),Date.valueOf(date));
        if(rows.isEmpty())return new DayUsage(date,BigDecimal.ZERO,BigDecimal.ZERO,breached);
        return new DayUsage(date,(BigDecimal)rows.getFirst().get("held_tokens"),(BigDecimal)rows.getFirst().get("spent_tokens"),breached);
    }
    private void add(RunLedger.Scope scope,LocalDate date,BigDecimal held,BigDecimal spent) {
        int changed=jdbc.update("""
                UPDATE oa_model_token_day SET held_tokens=held_tokens+?,spent_tokens=spent_tokens+?
                WHERE tenant_id=? AND environment=? AND budget_day=?
                """,held,spent,scope.tenant(),scope.environment(),Date.valueOf(date));
        if(changed!=1)throw new IllegalStateException("Budget day is missing");
    }
    private Reservation required(RunLedger.Scope scope,String id,String fingerprint) {
        var value=find(scope,id);
        if(value==null)throw new RunFailure("token_reservation_missing");
        match(value,fingerprint);return value;
    }
    private Reservation find(RunLedger.Scope scope,String id) {
        var rows=jdbc.queryForList("SELECT * FROM oa_model_token_reservation WHERE tenant_id=? AND environment=? AND call_id=? FOR UPDATE",
                scope.tenant(),scope.environment(),id);
        if(rows.isEmpty())return null;Map<String,Object> row=rows.getFirst();
        return new Reservation(id,(String)row.get("request_sha256"),((Date)row.get("budget_day")).toLocalDate(),
                ((Number)row.get("bound_tokens")).longValue(),(String)row.get("phase"),
                row.get("input_tokens")==null?null:((Number)row.get("input_tokens")).longValue(),
                row.get("output_tokens")==null?null:((Number)row.get("output_tokens")).longValue());
    }
    private static void match(Reservation value,String fingerprint) {
        if(!value.fingerprint().equals(fingerprint))throw new RunFailure("token_request_conflict");
    }
    private static void identity(String id,String fingerprint) {
        try {if(id==null || !UUID.fromString(id).toString().equals(id))throw new IllegalArgumentException();}
        catch(IllegalArgumentException invalid){throw new RunFailure("invalid_token_call");}
        if(fingerprint==null || !fingerprint.matches("[a-f0-9]{64}"))throw new RunFailure("invalid_token_call");
    }
    private java.time.Instant now() {
        if(clock!=null)return clock.instant(); // Explicit clock only for deterministic boundary tests.
        // All API replicas use the same database clock; never reset a budget on host clock skew.
        var epoch=jdbc.queryForObject("SELECT UNIX_TIMESTAMP(CURRENT_TIMESTAMP(3))",BigDecimal.class);
        return java.time.Instant.ofEpochMilli(epoch.movePointRight(3).longValueExact());
    }
    private <T> T tx(Supplier<T> operation) {return transaction.execute(ignored->operation.get());}
}
