package com.iflytek.astron.console.hub.platform;

import com.iflytek.astron.console.commons.tenant.TenantAccessService;
import com.iflytek.astron.console.commons.tenant.TenantPermission;
import java.sql.Timestamp;
import java.time.Instant;
import java.util.LinkedHashMap;
import java.util.Map;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.annotation.Transactional;

/** Counts retained task rows in an explicit time window, independent of list pagination. */
@Service
public class PlatformStatisticsService {
    private final JdbcTemplate jdbc;
    private final TenantAccessService access;
    public PlatformStatisticsService(JdbcTemplate jdbc, TenantAccessService access) {
        this.jdbc = jdbc; this.access = access;
    }

    @Transactional(readOnly=true, isolation=Isolation.REPEATABLE_READ)
    public Map<String,Object> snapshot(String tenant, String environment, String window) {
        access.require(tenant,environment,TenantPermission.LOG_READ);
        long seconds = switch(window) {
            case "1h" -> 3600; case "24h" -> 86400; case "7d" -> 604800;
            default -> throw new IllegalArgumentException("Unsupported statistics window");
        };
        Instant end=Instant.now(), start=end.minusSeconds(seconds);
        Object[] scope={tenant,environment,Timestamp.from(start),Timestamp.from(end)};
        String range=" WHERE tenant_id=? AND environment=? AND created_at>=? AND created_at<?";
        var totals=jdbc.queryForMap("""
                SELECT COUNT(*) AS total, COUNT(usage_json) AS reportedUsageRuns,
                  SUM(CAST(JSON_UNQUOTE(JSON_EXTRACT(usage_json,'$.total_tokens')) AS DECIMAL(30,0))) AS totalTokens,
                  SUM(CAST(JSON_UNQUOTE(JSON_EXTRACT(usage_json,'$.prompt_tokens')) AS DECIMAL(30,0))) AS promptTokens,
                  SUM(CAST(JSON_UNQUOTE(JSON_EXTRACT(usage_json,'$.completion_tokens')) AS DECIMAL(30,0))) AS completionTokens,
                  AVG(TIMESTAMPDIFF(MICROSECOND,created_at,started_at)/1000000.0) AS meanQueueSeconds,
                  AVG(TIMESTAMPDIFF(MICROSECOND,started_at,finished_at)/1000000.0) AS meanRunSeconds,
                  COUNT(started_at) AS startedRuns,
                  COUNT(CASE WHEN started_at IS NOT NULL THEN finished_at END) AS timedRuns
                FROM oa_agent_run
                """+range,scope);
        var states=jdbc.queryForList("SELECT state,COUNT(*) AS count FROM oa_agent_run"+range+" GROUP BY state ORDER BY state",scope);
        var current=jdbc.queryForMap("""
                SELECT COUNT(CASE WHEN state='queued' THEN 1 END) AS queued,
                  COUNT(CASE WHEN state='running' THEN 1 END) AS running,
                  COUNT(CASE WHEN state='cancel_requested' THEN 1 END) AS cancelling,
                  COALESCE(MAX(CASE WHEN state='queued' THEN TIMESTAMPDIFF(SECOND,created_at,CURRENT_TIMESTAMP(3)) END),0) AS oldestQueueSeconds
                FROM oa_agent_run WHERE tenant_id=? AND environment=? AND state IN ('queued','running','cancel_requested')
                """,tenant,environment);
        current.put("occupiedExecutionSlots",jdbc.queryForObject("SELECT COUNT(*) FROM oa_run_attempt WHERE tenant_id=? AND environment=? AND lease_state<>'released'",Long.class,tenant,environment));
        current.put("unreleasedSandboxes",jdbc.queryForObject("SELECT COUNT(*) FROM oa_run_sandbox WHERE tenant_id=? AND environment=? AND phase<>'released'",Long.class,tenant,environment));
        current.put("cleanupPendingSandboxes",jdbc.queryForObject("SELECT COUNT(*) FROM oa_run_sandbox WHERE tenant_id=? AND environment=? AND phase='cleanup_pending'",Long.class,tenant,environment));
        var result=new LinkedHashMap<String,Object>();
        result.put("window",window);result.put("windowStart",start);result.put("windowEnd",end);
        result.put("sampledAt",end);result.put("totals",totals);result.put("states",states);result.put("current",current);
        return result;
    }
}
