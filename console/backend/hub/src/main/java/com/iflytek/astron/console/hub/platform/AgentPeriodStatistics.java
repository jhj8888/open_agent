package com.iflytek.astron.console.hub.platform;

import com.iflytek.astron.console.commons.tenant.TenantAccessService;
import com.iflytek.astron.console.commons.tenant.TenantPermission;
import java.sql.Timestamp;
import java.time.*;
import java.time.temporal.TemporalAdjusters;
import java.util.*;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.annotation.Transactional;

/** Natural periods; leaf model usage is independent of task creation time/list pagination. */
@Service
public class AgentPeriodStatistics {
    private final JdbcTemplate jdbc;
    private final TenantAccessService access;
    private final Clock clock;
    public static final ZoneId ZONE=ZoneId.of("Asia/Shanghai");
    @Autowired public AgentPeriodStatistics(JdbcTemplate jdbc,TenantAccessService access) {this(jdbc,access,Clock.systemUTC());}
    public AgentPeriodStatistics(JdbcTemplate jdbc,TenantAccessService access,Clock clock) {this.jdbc=jdbc;this.access=access;this.clock=clock;}
    public record Period(Instant start,Instant end) {}
    public static Period period(String value,Instant now) {
        var today=now.atZone(ZONE).toLocalDate();
        LocalDate start,end;
        switch(value) {
            case "this_week" -> {start=today.with(TemporalAdjusters.previousOrSame(DayOfWeek.MONDAY));end=start.plusWeeks(1);}
            case "last_week" -> {end=today.with(TemporalAdjusters.previousOrSame(DayOfWeek.MONDAY));start=end.minusWeeks(1);}
            case "this_month" -> {start=today.withDayOfMonth(1);end=start.plusMonths(1);}
            case "last_month" -> {end=today.withDayOfMonth(1);start=end.minusMonths(1);}
            default -> throw new IllegalArgumentException("Unsupported observation period");
        }
        return new Period(start.atStartOfDay(ZONE).toInstant(),end.atStartOfDay(ZONE).toInstant());
    }
    @Transactional(readOnly=true,isolation=Isolation.REPEATABLE_READ,timeout=15)
    public Map<String,Object> snapshot(String tenant,String environment,String value) {
        access.require(tenant,environment,TenantPermission.LOG_READ);
        Instant now=clock.instant();var period=period(value,now);Instant through=now.isBefore(period.end())?now:period.end();
        Object[] args={tenant,environment,Timestamp.from(period.start()),Timestamp.from(through)};
        String tasks=" FROM oa_agent_run WHERE tenant_id=? AND environment=? AND created_at>=? AND created_at<?";
        String steps=" FROM oa_run_step WHERE tenant_id=? AND environment=? AND ended_at>=? AND ended_at<?";
        var taskTotals=jdbc.queryForMap("""
                SELECT COUNT(*) AS tasks,COUNT(finished_at) AS finishedTasks,
                  COUNT(CASE WHEN state='succeeded' THEN 1 END) AS succeededTasks,
                  COUNT(CASE WHEN state='failed' THEN 1 END) AS failedTasks,
                  COUNT(CASE WHEN state='cancelled' THEN 1 END) AS cancelledTasks,
                  COUNT(CASE WHEN state='timed_out' THEN 1 END) AS timedOutTasks,
                  AVG(TIMESTAMPDIFF(MICROSECOND,started_at,finished_at)/1000.0) AS meanTaskMs
                """+tasks,args);
        taskTotals.put("p95TaskMs",percentile("TIMESTAMPDIFF(MICROSECOND,started_at,finished_at)/1000.0",tasks+" AND started_at IS NOT NULL AND finished_at IS NOT NULL",args));
        var usage=jdbc.queryForMap("""
                SELECT COUNT(*) AS modelCalls,COUNT(CASE WHEN usage_source='unknown' THEN 1 END) AS unknownModelCalls,
                  COALESCE(SUM(input_tokens),0) AS inputTokens,COALESCE(SUM(output_tokens),0) AS outputTokens,
                  SUM(cache_read_tokens) AS cacheReadTokens,SUM(cache_write_tokens) AS cacheWriteTokens,
                  AVG(duration_ms) AS meanModelMs,COUNT(CASE WHEN status<>'succeeded' THEN 1 END) AS failedModelCalls
                """+steps+" AND kind='model'",args);
        usage.put("p95ModelMs",percentile("duration_ms",steps+" AND kind='model' AND duration_ms IS NOT NULL",args));
        usage.put("incompleteModelCalls",jdbc.queryForObject("SELECT COUNT(*) FROM oa_run_step WHERE tenant_id=? AND environment=? AND started_at>=? AND started_at<? AND kind='model' AND ended_at IS NULL",Long.class,args));
        var days=new LinkedHashMap<String,Map<String,Object>>();
        for(LocalDate day=period.start().atZone(ZONE).toLocalDate();day.isBefore(period.end().atZone(ZONE).toLocalDate());day=day.plusDays(1)) {
            var row=new LinkedHashMap<String,Object>();row.put("date",day.toString());
            for(String key:List.of("tasks","modelCalls","unknownModelCalls","inputTokens","outputTokens"))row.put(key,0L);
            days.put(day.toString(),row);
        }
        for(var row:jdbc.queryForList("SELECT DATE_FORMAT(TIMESTAMPADD(HOUR,8,created_at),'%Y-%m-%d') AS day,COUNT(*) AS tasks"+tasks+" GROUP BY day",args))
            days.get(row.get("day").toString()).put("tasks",row.get("tasks"));
        for(var row:jdbc.queryForList("""
                SELECT DATE_FORMAT(TIMESTAMPADD(HOUR,8,ended_at),'%Y-%m-%d') AS day,COUNT(*) AS modelCalls,
                  COUNT(CASE WHEN usage_source='unknown' THEN 1 END) AS unknownModelCalls,
                  COALESCE(SUM(input_tokens),0) AS inputTokens,COALESCE(SUM(output_tokens),0) AS outputTokens
                """+steps+" AND kind='model' GROUP BY day",args)) {
            var target=days.get(row.remove("day").toString());target.putAll(row);
        }
        var tools=jdbc.queryForList("SELECT name,COUNT(*) AS calls,COUNT(CASE WHEN status<>'succeeded' THEN 1 END) AS failedCalls,AVG(duration_ms) AS meanMs"+steps+" AND kind='tool' GROUP BY name ORDER BY calls DESC,name LIMIT 100",args);
        var models=jdbc.queryForList("SELECT name,COUNT(*) AS calls,COUNT(CASE WHEN usage_source='unknown' THEN 1 END) AS unknownCalls,COALESCE(SUM(input_tokens),0) AS inputTokens,COALESCE(SUM(output_tokens),0) AS outputTokens"+steps+" AND kind='model' GROUP BY name ORDER BY calls DESC,name LIMIT 100",args);
        // Group the observed model calls by immutable deployment, regardless of task creation date.
        var deployments=jdbc.queryForList("""
                SELECT r.deployment_id,COUNT(*) AS modelCalls,COALESCE(SUM(s.input_tokens),0) AS inputTokens,
                  COALESCE(SUM(s.output_tokens),0) AS outputTokens,COUNT(CASE WHEN s.usage_source='unknown' THEN 1 END) AS unknownCalls
                FROM oa_run_step s JOIN oa_agent_run r ON r.run_id=s.run_id
                WHERE s.tenant_id=? AND s.environment=? AND s.ended_at>=? AND s.ended_at<? AND s.kind='model'
                GROUP BY r.deployment_id ORDER BY modelCalls DESC,r.deployment_id LIMIT 100
                """,args);
        return Map.of("period",value,"timezone",ZONE.toString(),"start",period.start(),"end",period.end(),"asOf",through,
                "tasks",taskTotals,"usage",usage,"daily",List.copyOf(days.values()),"tools",tools,"breakdown",Map.of("models",models,"deployments",deployments));
    }
    private Number percentile(String expression,String query,Object[] args) {
        return jdbc.queryForObject("WITH durations AS (SELECT "+expression+" AS ms"+query+
                "), ranked AS (SELECT ms,ROW_NUMBER() OVER(ORDER BY ms) AS position,COUNT(*) OVER() AS total FROM durations) "+
                "SELECT MAX(CASE WHEN position=CEIL(total*0.95) THEN ms END) FROM ranked",Number.class,args);
    }
}
