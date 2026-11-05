package com.iflytek.astron.console.hub.platform.run;

import com.fasterxml.jackson.databind.ObjectMapper;
import java.sql.Timestamp;
import java.time.Instant;
import java.util.*;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.TransactionDefinition;
import org.springframework.transaction.support.TransactionTemplate;

/** Durable, revision-fenced observation delivery. No business input, secrets or execution methods. */
@Component
public class ObservationOutbox {
    private final JdbcTemplate jdbc;
    private final ObjectMapper json;
    private final TransactionTemplate transaction;
    public ObservationOutbox(JdbcTemplate jdbc,ObjectMapper json,PlatformTransactionManager manager) {
        this.jdbc=jdbc;this.json=json;transaction=new TransactionTemplate(manager);
        transaction.setIsolationLevel(TransactionDefinition.ISOLATION_READ_COMMITTED);transaction.setTimeout(10);
    }
    public record Claim(long id,String claimId,long revision,Instant claimUntil,long deliveryAttempt) {}

    /** Called only inside the existing run/attempt write transaction, after a real new callback. */
    static void changed(JdbcTemplate jdbc,RunLedger.Lease lease) {
        jdbc.update("""
                INSERT INTO oa_observation_outbox(tenant_id,environment,run_id,attempt_no) VALUES(?,?,?,?)
                ON DUPLICATE KEY UPDATE revision=revision+1,available_at=CURRENT_TIMESTAMP(3)
                """,lease.scope().tenant(),lease.scope().environment(),lease.runId(),lease.attempt());
    }

    public List<Claim> claim(String owner,int leaseSeconds) {
        if(leaseSeconds<30||leaseSeconds>300)throw new RunFailure("invalid_observation_claim");
        return transaction.execute(status->{
            // Only the outbox row is locked. Callback writers lock run -> step -> outbox.
            var rows=jdbc.queryForList("""
                    SELECT o.* FROM oa_observation_outbox o
                    WHERE o.revision>o.acked_revision AND o.available_at<=CURRENT_TIMESTAMP(3)
                      AND (o.claim_until IS NULL OR o.claim_until<=CURRENT_TIMESTAMP(3))
                      AND EXISTS(SELECT 1 FROM oa_agent_run r WHERE r.run_id=o.run_id
                        AND r.state IN ('succeeded','failed','timed_out','cancelled'))
                      AND EXISTS(SELECT 1 FROM oa_run_attempt a WHERE a.run_id=o.run_id
                        AND a.attempt_no=o.attempt_no AND a.lease_state='released')
                    ORDER BY o.available_at,o.id LIMIT 1 FOR UPDATE SKIP LOCKED
                    """);
            if(rows.isEmpty())return List.of();
            var row=rows.getFirst();String claim=UUID.randomUUID().toString();
            Instant until=jdbc.queryForObject("SELECT CURRENT_TIMESTAMP(3)",Timestamp.class).toInstant().plusSeconds(leaseSeconds);
            long id=number(row,"id"),attempt=number(row,"delivery_attempts")+1;
            jdbc.update("UPDATE oa_observation_outbox SET claim_id=?,claim_owner=?,claim_until=?,delivery_attempts=? WHERE id=?",
                    claim,owner,Timestamp.from(until),attempt,id);
            return List.of(new Claim(id,claim,number(row,"revision"),until,attempt));
        });
    }

    private List<Map<String,Object>> owned(long id,String owner,String claim,long revision) {
        return jdbc.queryForList("""
                SELECT * FROM oa_observation_outbox WHERE id=? AND claim_owner=? AND claim_id=?
                  AND revision=? AND acked_revision<revision AND claim_until>CURRENT_TIMESTAMP(3) FOR UPDATE
                """,id,owner,claim,revision);
    }

    public Map<String,Object> page(long id,String owner,String claim,long revision,long after) {
        if(after<0)throw new RunFailure("invalid_cursor");
        return transaction.execute(status->{
            var rows=owned(id,owner,claim,revision);
            if(rows.isEmpty())throw new RunFailure("observation_claim_lost");
            var delivery=rows.getFirst();String run=(String)delivery.get("run_id");
            var meta=jdbc.queryForMap("""
                    SELECT r.tenant_id,r.environment,r.run_id,r.deployment_id,r.state,
                      a.attempt_no,a.created_at,a.released_at
                    FROM oa_agent_run r JOIN oa_run_attempt a ON r.run_id=a.run_id
                    WHERE r.run_id=? AND a.attempt_no=? AND a.lease_state='released'
                    """,run,delivery.get("attempt_no"));
            meta.put("created_at",((Timestamp)meta.get("created_at")).toInstant());
            meta.put("released_at",((Timestamp)meta.get("released_at")).toInstant());
            var steps=jdbc.queryForList("""
                    SELECT id,start_json,finish_json FROM oa_run_step
                    WHERE run_id=? AND attempt_no=? AND id>? ORDER BY id LIMIT 33
                    """,run,delivery.get("attempt_no"),after);
            boolean more=steps.size()>32;if(more)steps.removeLast();
            for(var step:steps)for(String field:List.of("start_json","finish_json")) {
                Object raw=step.get(field);
                try{step.put(field,raw==null?null:json.readTree(raw.toString()));}
                catch(Exception invalid){throw new IllegalStateException("Invalid stored observation");}
            }
            return Map.of("run",meta,"items",steps,"hasMore",more,"next",steps.isEmpty()?after:steps.getLast().get("id"));
        });
    }

    public boolean acknowledge(long id,String owner,String claim,long revision) {
        return jdbc.update("""
                UPDATE oa_observation_outbox SET acked_revision=revision,claim_id=NULL,claim_owner=NULL,
                  claim_until=NULL,last_error=NULL,delivered_at=CURRENT_TIMESTAMP(3)
                WHERE id=? AND claim_owner=? AND claim_id=? AND revision=? AND acked_revision<revision
                  AND claim_until>CURRENT_TIMESTAMP(3)
                """,id,owner,claim,revision)==1;
    }

    public boolean retry(long id,String owner,String claim,long revision,int delay,String error) {
        if(delay<1||delay>300||error==null||!Set.of("backend_unavailable","export_rejected","payload_limit","control_unavailable","invalid_projection").contains(error))
            throw new RunFailure("invalid_observation_retry");
        // A newer revision may have arrived: relinquish the old lease without acknowledging it.
        return jdbc.update("""
                UPDATE oa_observation_outbox SET claim_id=NULL,claim_owner=NULL,claim_until=NULL,
                  available_at=TIMESTAMPADD(SECOND,?,CURRENT_TIMESTAMP(3)),last_error=?
                WHERE id=? AND claim_owner=? AND claim_id=? AND revision>=? AND claim_until>CURRENT_TIMESTAMP(3)
                """,delay,error,id,owner,claim,revision)==1;
    }
    private static long number(Map<String,Object> row,String field){return ((Number)row.get(field)).longValue();}
}
