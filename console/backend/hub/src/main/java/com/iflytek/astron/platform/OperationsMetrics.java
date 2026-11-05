package com.iflytek.astron.platform;

import io.micrometer.core.instrument.Gauge;
import io.micrometer.prometheusmetrics.PrometheusConfig;
import io.micrometer.prometheusmetrics.PrometheusMeterRegistry;
import jakarta.annotation.PreDestroy;
import java.time.Instant;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.atomic.AtomicReference;
import org.slf4j.LoggerFactory;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

/** Dedicated official registry with finite labels; no task identifiers or payloads. */
@Component
public class OperationsMetrics implements AutoCloseable {
    private record Sample(long at, Map<String,Double> values) {}
    private final PrometheusMeterRegistry registry=new PrometheusMeterRegistry(PrometheusConfig.DEFAULT);
    private final AtomicReference<Sample> sample=new AtomicReference<>(new Sample(0,Map.of()));
    private final JdbcTemplate jdbc;
    private volatile boolean lastCollectionSucceeded;

    public OperationsMetrics(JdbcTemplate jdbc) {
        this.jdbc=jdbc;
        Gauge.builder("open.agent.metrics.collection.success",this,m->m.fresh()?1:0).register(registry);
        Gauge.builder("open.agent.metrics.collected.timestamp.seconds",this,m->m.sample.get().at()).register(registry);
        for (String env:List.of("test","prod")) {
            for (String state:List.of("queued","running","cancel_requested"))
                gauge("open.agent.runs.current",env,"state",state,"active."+env+"."+state);
            for (String state:List.of("succeeded","failed","timed_out","cancelled"))
                gauge("open.agent.runs.finished.five.minutes",env,"state",state,"finished."+env+"."+state);
            for (String phase:List.of("reserved","creating","created","cleanup_pending"))
                gauge("open.agent.sandboxes.current",env,"phase",phase,"sandbox."+env+"."+phase);
            for (String metric:List.of("execution.slots.occupied","execution.leases.expired","queue.oldest.seconds","cleanup.oldest.seconds","observations.pending","observations.failed"))
                Gauge.builder("open.agent."+metric,this,m->m.value(metric+"."+env)).tag("environment",env).register(registry);
        }
    }

    private void gauge(String name,String env,String label,String value,String key) {
        Gauge.builder(name,this,m->m.value(key)).tags("environment",env,label,value).register(registry);
    }
    private boolean fresh() { return lastCollectionSucceeded && Instant.now().getEpochSecond()-sample.get().at()<=60; }
    private double value(String key) { return fresh()?sample.get().values().getOrDefault(key,0.0):Double.NaN; }
    public String scrape() { return registry.scrape(); }

    @Scheduled(fixedDelay=15000, initialDelay=1000)
    public void collect() {
        try {
            var values=new HashMap<String,Double>();
            for (String env:List.of("test","prod")) {
                grouped(values,"active."+env,"state",jdbc.queryForList("SELECT state,COUNT(*) AS count FROM oa_agent_run WHERE environment=? AND state IN ('queued','running','cancel_requested') GROUP BY state",env));
                grouped(values,"finished."+env,"state",jdbc.queryForList("SELECT state,COUNT(*) AS count FROM oa_agent_run WHERE environment=? AND finished_at>=TIMESTAMPADD(MINUTE,-5,CURRENT_TIMESTAMP(3)) AND finished_at<=CURRENT_TIMESTAMP(3) GROUP BY state",env));
                grouped(values,"sandbox."+env,"phase",jdbc.queryForList("SELECT phase,COUNT(*) AS count FROM oa_run_sandbox WHERE environment=? AND phase<>'released' GROUP BY phase",env));
                values.put("execution.slots.occupied."+env,number("SELECT COUNT(*) FROM oa_run_attempt WHERE environment=? AND lease_state<>'released'",env));
                values.put("execution.leases.expired."+env,number("SELECT COUNT(*) FROM oa_run_attempt WHERE environment=? AND lease_state<>'released' AND heartbeat_until<CURRENT_TIMESTAMP(3)",env));
                values.put("queue.oldest.seconds."+env,number("SELECT COALESCE(MAX(TIMESTAMPDIFF(SECOND,created_at,CURRENT_TIMESTAMP(3))),0) FROM oa_agent_run WHERE environment=? AND state='queued'",env));
                values.put("cleanup.oldest.seconds."+env,number("SELECT COALESCE(MAX(TIMESTAMPDIFF(SECOND,reserved_at,CURRENT_TIMESTAMP(3))),0) FROM oa_run_sandbox WHERE environment=? AND phase IN ('creating','cleanup_pending')",env));
                values.put("observations.pending."+env,number("SELECT COUNT(*) FROM oa_observation_outbox WHERE environment=? AND revision>acked_revision",env));
                values.put("observations.failed."+env,number("SELECT COUNT(*) FROM oa_observation_outbox WHERE environment=? AND revision>acked_revision AND last_error IS NOT NULL",env));
            }
            sample.set(new Sample(Instant.now().getEpochSecond(),Map.copyOf(values)));
            lastCollectionSucceeded=true;
        } catch (RuntimeException unavailable) {
            // SQL, request data and credentials never enter monitoring logs.
            if (lastCollectionSucceeded) LoggerFactory.getLogger(getClass()).warn("Operational metrics collection unavailable; snapshot withheld");
            lastCollectionSucceeded=false;
        }
    }
    private double number(String sql,String env) { return jdbc.queryForObject(sql,Double.class,env); }
    private static void grouped(Map<String,Double> values,String prefix,String column,List<Map<String,Object>> rows) {
        for (var row:rows) values.put(prefix+"."+row.get(column),((Number)row.get("count")).doubleValue());
    }
    @PreDestroy public void close() { registry.close(); }
}
