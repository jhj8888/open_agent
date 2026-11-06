package com.iflytek.astron.console.hub.platform.run;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.iflytek.astron.console.commons.tenant.*;
import java.nio.charset.StandardCharsets;
import java.sql.Timestamp;
import java.time.Instant;
import java.util.*;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.TransactionDefinition;
import org.springframework.transaction.support.TransactionTemplate;

/** JWT workbench history; deployment service keys retain their existing deployment-scoped API. */
@Service
public class TaskHistoryService {
    public record Page(List<Map<String,Object>> items, String next, boolean hasMore) {}
    private final JdbcTemplate jdbc;
    private final TenantAccessService access;
    private final AgentDeploymentService deployments;
    private final ObjectMapper json;
    private final TransactionTemplate tx;

    public TaskHistoryService(JdbcTemplate jdbc, TenantAccessService access, AgentDeploymentService deployments,
            ObjectMapper json, PlatformTransactionManager manager) {
        this.jdbc=jdbc;this.access=access;this.deployments=deployments;this.json=json;
        tx=new TransactionTemplate(manager);tx.setIsolationLevel(TransactionDefinition.ISOLATION_READ_COMMITTED);tx.setTimeout(15);
    }

    public Page list(RunLedger.Scope scope, int limit, String before, String state, String search) {
        if(limit<1 || limit>100 || state==null || !Set.of("all","active","succeeded","failed","cancelled").contains(state)
                || search==null || search.length()>128 || search.codePoints().anyMatch(Character::isISOControl))
            throw new RunFailure("invalid_history_query");
        String term=search.strip();
        return tx.execute(status -> {
            deployments.lock(scope);
            var context=access.require(scope.tenant(),scope.environment(),TenantPermission.METADATA_READ);
            boolean all=TenantAccessPolicy.allows(context.role(),context.environment(),context.status(),TenantPermission.LOG_READ);
            if(!all && (!scope.environment().equals("prod") || !context.status().equals("active")
                    || jdbc.queryForList("SELECT uid FROM oa_deployment_grant WHERE tenant_id=? AND environment=? AND uid=? LIMIT 1",
                        scope.tenant(),scope.environment(),context.uid()).isEmpty())) throw new RunFailure("action_forbidden");
            String binding=RunJson.hash(scope.tenant()+"\n"+scope.environment()+"\n"+RunJson.hash(context.uid())+"\n"+limit+"\n"+state+"\n"+term);
            var query=new StringBuilder("""
                    SELECT r.run_id,r.deployment_id,d.display_name AS agent_name,r.state,r.trace_id,r.error_code,
                      r.usage_json,r.created_at,r.started_at,r.finished_at,r.deadline_at
                    FROM oa_agent_run r JOIN oa_agent_deployment d
                      ON d.tenant_id=r.tenant_id AND d.environment=r.environment AND d.deployment_id=r.deployment_id
                    WHERE r.tenant_id=? AND r.environment=?
                      AND NOT EXISTS(SELECT 1 FROM oa_run_deletion x WHERE x.run_id=r.run_id)
                    """);
            List<Object> args=new ArrayList<>(List.of(scope.tenant(),scope.environment()));
            if(!all) {
                query.append(" AND r.actor_id=? AND EXISTS(SELECT 1 FROM oa_deployment_grant g WHERE g.tenant_id=r.tenant_id AND g.environment=r.environment AND g.deployment_id=r.deployment_id AND g.uid=?)");
                args.add(new DeploymentAuthorization.Caller(context.uid(),null).actor());args.add(context.uid());
            }
            query.append(switch(state) {
                case "active" -> " AND r.state IN ('queued','running','cancel_requested')";
                case "failed" -> " AND r.state IN ('failed','timed_out')";
                case "succeeded" -> " AND r.state='succeeded'";
                case "cancelled" -> " AND r.state='cancelled'";
                default -> "";
            });
            if(!term.isEmpty()) {
                String literal=term.replace("!","!!").replace("%","!%").replace("_","!_");
                // run_id is ASCII: do not ask MySQL to convert a Unicode name parameter to that charset.
                if(term.matches("[A-Fa-f0-9-]{1,36}")) {
                    query.append(" AND (r.run_id LIKE ? OR d.display_name LIKE ? ESCAPE '!')");
                    args.add(term.toLowerCase(Locale.ROOT)+"%");
                } else query.append(" AND (d.display_name LIKE ? ESCAPE '!')");
                args.add("%"+literal+"%");
            }
            if(before!=null && !before.isEmpty()) {
                var cursor=decode(before,binding);
                query.append(" AND (r.created_at<? OR (r.created_at=? AND r.run_id<?))");
                args.add(cursor.time());args.add(cursor.time());args.add(cursor.id());
            }
            query.append(" ORDER BY r.created_at DESC,r.run_id DESC LIMIT ?");args.add(limit+1);
            var rows=jdbc.queryForList(query.toString(),args.toArray());
            boolean more=rows.size()>limit;
            if(more) rows.removeLast();
            String next=more?encode(rows.getLast(),binding):null;
            for(var row:rows) {
                Object usage=row.remove("usage_json");
                try { row.put("usage",usage==null?null:json.readTree(usage.toString())); }
                catch(Exception invalid) { throw new RunFailure("stored_event_unavailable"); }
            }
            return new Page(rows,next,more);
        });
    }

    private record Cursor(Timestamp time,String id) {}
    private Cursor decode(String value,String binding) {
        try {
            if(value.length()>512 || !value.matches("[A-Za-z0-9_-]+")) throw new IllegalArgumentException();
            String[] parts=new String(Base64.getUrlDecoder().decode(value),StandardCharsets.UTF_8).split("\\n",-1);
            if(parts.length!=4 || !parts[0].equals("1") || !parts[1].equals(binding)
                    || !UUID.fromString(parts[3]).toString().equals(parts[3])) throw new IllegalArgumentException();
            var time=Instant.parse(parts[2]);
            int year=time.atOffset(java.time.ZoneOffset.UTC).getYear();
            if(year<1000 || year>9999 || time.getNano()%1_000_000!=0) throw new IllegalArgumentException();
            return new Cursor(Timestamp.from(time),parts[3]);
        } catch(RuntimeException invalid) { throw new RunFailure("invalid_cursor"); }
    }
    private String encode(Map<String,Object> row,String binding) {
        String value="1\n"+binding+"\n"+((Timestamp)row.get("created_at")).toInstant()+"\n"+row.get("run_id");
        return Base64.getUrlEncoder().withoutPadding().encodeToString(value.getBytes(StandardCharsets.UTF_8));
    }
}
