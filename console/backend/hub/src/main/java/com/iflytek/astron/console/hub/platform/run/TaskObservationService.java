package com.iflytek.astron.console.hub.platform.run;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.sql.Timestamp;
import java.time.Instant;
import java.util.*;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.support.TransactionTemplate;

/** Official framework callbacks enter here through the authenticated worker transport only. */
@Service
public class TaskObservationService {
    private final RunLedger ledger;
    private final JdbcTemplate jdbc;
    private final ObjectMapper json;
    private final DeploymentAuthorization authorization;
    private final AgentDeploymentService deployments;
    private final TransactionTemplate transaction;
    public TaskObservationService(RunLedger ledger,JdbcTemplate jdbc,ObjectMapper json,
            DeploymentAuthorization authorization,AgentDeploymentService deployments,PlatformTransactionManager manager) {
        this.ledger=ledger;this.jdbc=jdbc;this.json=json;this.authorization=authorization;this.deployments=deployments;
        this.transaction=new TransactionTemplate(manager);this.transaction.setTimeout(15);
    }
    public record Step(String stepId,String parentStepId,String kind,String name,Instant startedAt,
            Instant endedAt,Long durationMs,String status,String usageSource,Long inputTokens,Long outputTokens,
            Long cacheReadTokens,Long cacheWriteTokens,JsonNode summary,
            @com.fasterxml.jackson.annotation.JsonInclude(com.fasterxml.jackson.annotation.JsonInclude.Include.NON_NULL) JsonNode content) {}
    public Map<String,Object> record(RunLedger.Lease lease,String phase,Step step) {
        validate(phase,step);
        String body=RunJson.encode(json,json.valueToTree(step),131072),hash=RunJson.hash(body);
        // Identical retransmission is safe, including after cancellation. No new execution is authorized here.
        return ledger.withAttempt(lease,()-> {
            var rows=jdbc.queryForList("SELECT * FROM oa_run_step WHERE run_id=? AND attempt_no=? AND step_id=?",
                    lease.runId(),lease.attempt(),step.stepId());
            if(phase.equals("start")) {
                if(!rows.isEmpty()) {
                    if(!hash.equals(rows.getFirst().get("start_hash")))throw new RunFailure("observation_conflict");
                    return Map.of("accepted",true);
                }
                return ledger.withPermit(lease,()-> {
                    long count=jdbc.queryForObject("SELECT COUNT(*) FROM oa_run_step WHERE run_id=? AND attempt_no=?",Long.class,lease.runId(),lease.attempt());
                    if(count>=4096)throw new RunFailure("observation_limit");
                    Instant now=Instant.now();
                    var run=ledger.get(lease.scope(),lease.runId());
                    Instant created=((Timestamp)run.get("created_at")).toInstant();
                    if(step.startedAt().isBefore(created.minusSeconds(60))||step.startedAt().isAfter(now.plusSeconds(60)))throw new RunFailure("invalid_observation");
                    jdbc.update("""
                            INSERT INTO oa_run_step(tenant_id,environment,run_id,attempt_no,step_id,parent_step_id,kind,name,started_at,usage_source,start_json,start_hash)
                            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                            """,lease.scope().tenant(),lease.scope().environment(),lease.runId(),lease.attempt(),step.stepId(),step.parentStepId(),
                            step.kind(),step.name(),Timestamp.from(step.startedAt()),step.usageSource(),boundedContent(lease,step,body),hash);
                    ObservationOutbox.changed(jdbc,lease);
                    return Map.of("accepted",true);
                });
            }
            if(rows.isEmpty())throw new RunFailure("observation_start_missing");
            var row=rows.getFirst();
            if(row.get("finish_hash")!=null) {
                if(!hash.equals(row.get("finish_hash")))throw new RunFailure("observation_conflict");
                return Map.of("accepted",true);
            }
            if(!Objects.equals(row.get("parent_step_id"),step.parentStepId()) || !row.get("kind").equals(step.kind()) || !row.get("name").equals(step.name())
                    || !((Timestamp)row.get("started_at")).toInstant().equals(step.startedAt()) || step.endedAt().isAfter(Instant.now().plusSeconds(60)))throw new RunFailure("invalid_observation");
            jdbc.update("""
                    UPDATE oa_run_step SET ended_at=?,duration_ms=?,status=?,usage_source=?,input_tokens=?,output_tokens=?,cache_read_tokens=?,cache_write_tokens=?,finish_json=?,finish_hash=? WHERE id=?
                    """,Timestamp.from(step.endedAt()),step.durationMs(),step.status(),step.usageSource(),step.inputTokens(),step.outputTokens(),step.cacheReadTokens(),step.cacheWriteTokens(),boundedContent(lease,step,body),hash,row.get("id"));
            ObservationOutbox.changed(jdbc,lease);
            return Map.of("accepted",true);
        });
    }
    private String boundedContent(RunLedger.Lease lease,Step step,String body) {
        if(step.content()==null)return body;
        long bytes=RunJson.encode(json,step.content(),65536).getBytes(java.nio.charset.StandardCharsets.UTF_8).length;
        // withAttempt holds the run lock. Duplicate deliveries never consume quota twice.
        int reserved=jdbc.update("""
                UPDATE oa_run_attempt SET observation_content_bytes=observation_content_bytes+?
                WHERE run_id=? AND attempt_no=? AND observation_content_bytes+?<=4194304
                """,bytes,lease.runId(),lease.attempt(),bytes);
        if(reserved==1)return body;
        var limited=json.<com.fasterxml.jackson.databind.node.ObjectNode>valueToTree(step);
        limited.set("content",json.createObjectNode().put("availability","run_limit"));
        return RunJson.encode(json,limited,131072);
    }
    static void validateContent(JsonNode content) {
        if(content==null)return;
        if(!content.isObject()||content.size()>7)throw new RunFailure("invalid_observation");
        var fields=content.fieldNames();
        while(fields.hasNext()) {
            String field=fields.next();var value=content.get(field);
            if(field.equals("inputMode")) {
                if(!value.isTextual()||!Set.of("initial","append","context_changed","tool_arguments").contains(value.asText()))throw new RunFailure("invalid_observation");
            } else if(field.equals("previousStepId")) {
                if(!value.isTextual()||!id(value.asText()))throw new RunFailure("invalid_observation");
            } else {
                if(!Set.of("input","thinking","toolCalls","toolResult","output").contains(field)||!value.isObject()||value.size()!=5
                        ||!value.path("text").isTextual()||value.path("text").asText().getBytes(java.nio.charset.StandardCharsets.UTF_8).length>6144
                        ||value.path("text").asText().chars().anyMatch(c->(c<32&&c!=9&&c!=10&&c!=13)||c==127)
                        ||!Set.of("model_messages","tool_arguments","provider_reasoning","model_message","model_tool_calls","tool_message").contains(value.path("source").asText())
                        ||!Set.of("provided","partial","not_returned","not_applicable").contains(value.path("availability").asText())
                        ||!value.path("truncated").isBoolean()||!value.path("originalBytes").isIntegralNumber()
                        ||!value.path("originalBytes").canConvertToLong()||value.path("originalBytes").asLong()<0)throw new RunFailure("invalid_observation");
            }
        }
    }
    static void validate(String phase,Step step) {
        if(!Set.of("start","finish").contains(phase==null?"":phase) || step==null || !id(step.stepId())
                || (step.parentStepId()!=null&&(!id(step.parentStepId())||step.parentStepId().equals(step.stepId())))
                || !Set.of("agent","chain","model","tool","skill","artifact").contains(step.kind()==null?"":step.kind())
                || step.name()==null || !step.name().matches("[A-Za-z0-9_./: -]{1,128}") || step.startedAt()==null
                || step.startedAt().getNano()%1000000!=0 || step.summary()==null || !step.summary().isObject())throw new RunFailure("invalid_observation");
        validateContent(step.content());
        // Summary schema contains metadata only. Never accept raw prompts/arguments/headers as extra fields.
        var fields=step.summary().fieldNames();
        while(fields.hasNext()) {
            String field=fields.next();JsonNode value=step.summary().get(field);
            if(!Set.of("input","output","errorType","toolCallId","releaseId","manifestSha256","fileCount").contains(field)
                    || !value.isTextual() || value.textValue().length()>2048 || value.textValue().chars().anyMatch(c->c<32||c==127))throw new RunFailure("invalid_observation");
        }
        for(Long value:Arrays.asList(step.inputTokens(),step.outputTokens(),step.cacheReadTokens(),step.cacheWriteTokens()))
            if(value!=null&&(value<0||value>1000000000))throw new RunFailure("invalid_observation");
        if(phase.equals("start")) {
            if(!"running".equals(step.status())||step.endedAt()!=null||step.durationMs()!=null||step.inputTokens()!=null||step.outputTokens()!=null||step.cacheReadTokens()!=null||step.cacheWriteTokens()!=null)throw new RunFailure("invalid_observation");
        } else if(!Set.of("succeeded","failed","interrupted").contains(step.status()==null?"":step.status())||step.endedAt()==null||step.endedAt().getNano()%1000000!=0
                ||step.endedAt().isBefore(step.startedAt())||step.durationMs()==null||step.durationMs()<0||step.durationMs()>86400000)throw new RunFailure("invalid_observation");
        String source=step.usageSource();
        if(step.kind().equals("model")) {
            if(!Set.of("unknown","provider").contains(source==null?"":source))throw new RunFailure("invalid_observation");
            if("provider".equals(source)&&(step.inputTokens()==null||step.outputTokens()==null))throw new RunFailure("invalid_observation");
            if("unknown".equals(source)&&(step.inputTokens()!=null||step.outputTokens()!=null||step.cacheReadTokens()!=null||step.cacheWriteTokens()!=null))throw new RunFailure("invalid_observation");
            if(step.cacheReadTokens()!=null&&step.cacheReadTokens()>step.inputTokens())throw new RunFailure("invalid_observation");
        } else {
            String expected=Set.of("agent","chain").contains(step.kind())?"aggregate":"not_applicable";
            if(!expected.equals(source)||step.inputTokens()!=null||step.outputTokens()!=null||step.cacheReadTokens()!=null||step.cacheWriteTokens()!=null)throw new RunFailure("invalid_observation");
        }
    }
    private static boolean id(String value) {return value!=null&&value.matches("[A-Za-z0-9_.:-]{1,128}");}
    public Map<String,Object> list(DeploymentAuthorization.Caller caller,String runId,long after,int limit) {
        if(after<0||limit<1||limit>200)throw new RunFailure("invalid_cursor");
        var deployment=authorization.runDeployment(runId);
        return transaction.execute(ignored-> {
            deployments.lock(deployment.scope());
            authorization.require(caller,deployment,DeploymentAuthorization.Action.READ,runId);
            var run=ledger.get(deployment.scope(),runId);
            var rows=jdbc.queryForList("""
                    SELECT s.id,s.attempt_no,step_id,parent_step_id,kind,name,started_at,ended_at,duration_ms,status,usage_source,
                        input_tokens,output_tokens,cache_read_tokens,cache_write_tokens,start_json,finish_json,
                        CASE WHEN o.acked_revision=o.revision THEN 'delivered' ELSE 'pending' END AS export_state
                    FROM oa_run_step s LEFT JOIN oa_observation_outbox o ON o.run_id=s.run_id AND o.attempt_no=s.attempt_no
                    WHERE s.run_id=? AND s.id>? ORDER BY s.id LIMIT ?
                    """,runId,after,limit+1);
            boolean more=rows.size()>limit;
            if(more)rows.removeLast();
            boolean terminal=RunState.parse((String)run.get("state")).terminal();
            for(var row:rows) {
                if(terminal&&row.get("status").equals("running"))row.put("status","interrupted");
                for(String field:List.of("start_json","finish_json")) {
                    Object raw=row.get(field);
                    try {row.put(field,raw==null?null:json.readTree(raw.toString()));}
                    catch(Exception invalid){throw new IllegalStateException("Invalid stored observation");}
                }
            }
            var totals=jdbc.queryForMap("""
                    SELECT COUNT(*) AS steps,COUNT(CASE WHEN kind='model' THEN 1 END) AS modelCalls,
                      COUNT(CASE WHEN kind='tool' THEN 1 END) AS toolCalls,
                      COUNT(CASE WHEN kind='model' AND usage_source='unknown' THEN 1 END) AS unknownModelCalls,
                      COALESCE(SUM(CASE WHEN kind='model' THEN input_tokens END),0) AS inputTokens,
                      COALESCE(SUM(CASE WHEN kind='model' THEN output_tokens END),0) AS outputTokens,
                      SUM(CASE WHEN kind='model' THEN cache_read_tokens END) AS cacheReadTokens,
                      SUM(CASE WHEN kind='model' THEN input_tokens-cache_read_tokens END) AS uncachedInputTokens,
                      COUNT(CASE WHEN kind='model' AND cache_read_tokens IS NOT NULL THEN 1 END) AS cacheReportedModelCalls,
                      COUNT(CASE WHEN kind='model' AND cache_read_tokens IS NULL THEN 1 END) AS unknownCacheModelCalls
                    FROM oa_run_step WHERE run_id=?
                    """,runId);
            return Map.of("items",rows,"hasMore",more,"next",rows.isEmpty()?after:rows.getLast().get("id"),"totals",totals);
        });
    }
}
