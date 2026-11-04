package com.iflytek.astron.console.hub.platform.run;

import java.sql.Timestamp;
import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.TransactionDefinition;
import org.springframework.transaction.support.TransactionTemplate;

/** At-least-once dispatch storage. Temporal dispatch MUST use the stable run ID for deduplication. */
@Component
public class RunOutbox {
    private final JdbcTemplate jdbc;
    private final TransactionTemplate transaction;

    public RunOutbox(JdbcTemplate jdbc, PlatformTransactionManager manager) {
        this.jdbc = jdbc;
        transaction = new TransactionTemplate(manager);
        transaction.setIsolationLevel(TransactionDefinition.ISOLATION_READ_COMMITTED);
        transaction.setTimeout(10);
    }

    /** Identifiers only: input, results and model credentials never become dispatcher payloads. */
    public record Delivery(long id, String tenant, String environment, String runId,
            String operation, String claimId, Instant claimUntil, int deliveryAttempt) {}

    public List<Delivery> claim(int limit, int leaseSeconds) {
        if (limit < 1 || limit > 100 || leaseSeconds < 1 || leaseSeconds > 60) throw new RunFailure("invalid_dispatch_limit");
        return transaction.execute(status -> {
            var rows = jdbc.queryForList("""
                    SELECT * FROM oa_run_outbox
                    WHERE (state='pending' AND available_at<=CURRENT_TIMESTAMP(3))
                       OR (state='claimed' AND claim_until<=CURRENT_TIMESTAMP(3))
                    ORDER BY id LIMIT ? FOR UPDATE SKIP LOCKED
                    """, limit);
            Instant until = jdbc.queryForObject("SELECT CURRENT_TIMESTAMP(3)", Timestamp.class).toInstant().plusSeconds(leaseSeconds);
            List<Delivery> result = new ArrayList<>();
            for (var row : rows) {
                String claim = UUID.randomUUID().toString();
                long id = ((Number) row.get("id")).longValue();
                int attempt = ((Number) row.get("delivery_attempts")).intValue() + 1;
                jdbc.update("UPDATE oa_run_outbox SET state='claimed',claim_id=?,claim_until=?,delivery_attempts=? WHERE id=?",
                        claim, Timestamp.from(until), attempt, id);
                result.add(new Delivery(id, (String) row.get("tenant_id"), (String) row.get("environment"),
                        (String) row.get("run_id"), (String) row.get("operation"), claim, until, attempt));
            }
            return List.copyOf(result);
        });
    }

    public boolean delivered(Delivery delivery) {
        return jdbc.update("UPDATE oa_run_outbox SET state='delivered',delivered_at=CURRENT_TIMESTAMP(3),claim_until=NULL "
                + "WHERE id=? AND claim_id=? AND state='claimed' AND claim_until>CURRENT_TIMESTAMP(3)",
                delivery.id(), delivery.claimId()) == 1;
    }

    public boolean retryLater(Delivery delivery, int delaySeconds) {
        if (delaySeconds < 1 || delaySeconds > 300) throw new RunFailure("invalid_dispatch_delay");
        return jdbc.update("UPDATE oa_run_outbox SET state='pending',available_at=TIMESTAMPADD(SECOND,?,CURRENT_TIMESTAMP(3)),claim_id=NULL,claim_until=NULL "
                + "WHERE id=? AND claim_id=? AND state='claimed' AND claim_until>CURRENT_TIMESTAMP(3)",
                delaySeconds, delivery.id(), delivery.claimId()) == 1;
    }
}
