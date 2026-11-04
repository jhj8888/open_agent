package com.iflytek.astron.console.hub.platform.run;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.iflytek.astron.console.hub.platform.model.ModelConnectionService;
import com.iflytek.astron.console.hub.platform.skill.SkillArtifactStore;
import java.nio.charset.StandardCharsets;
import java.sql.Timestamp;
import java.util.List;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;

/** Resolves only the immutable resources bound to a live, authenticated execution lease. */
@Service
public class RunRuntimeService {
    private final RunLedger ledger;
    private final JdbcTemplate jdbc;
    private final ModelConnectionService models;
    private final SkillArtifactStore artifacts;
    private final ObjectMapper json;
    public RunRuntimeService(RunLedger ledger, JdbcTemplate jdbc, ModelConnectionService models, SkillArtifactStore artifacts, ObjectMapper json) {
        this.ledger=ledger; this.jdbc=jdbc; this.models=models; this.artifacts=artifacts; this.json=json;
    }
    public record SkillRef(String releaseId, String manifestSha256) {}
    public record RuntimeBundle(JsonNode input, JsonNode snapshot, ModelConnectionService.WorkerModel model,
            String deadline, List<SkillRef> skills) {
        @Override public String toString() { return "RuntimeBundle[private execution configuration]"; }
    }

    public RuntimeBundle bundle(RunLedger.Lease lease) {
        return ledger.withPermit(lease, () -> {
            var scope=lease.scope();
            var row=jdbc.queryForMap("""
                    SELECT r.input_json,r.snapshot_json,r.deadline_at,d.deployment_id,d.model_connection_id,d.model_version
                    FROM oa_agent_run r JOIN oa_agent_deployment d ON d.tenant_id=r.tenant_id AND d.environment=r.environment
                    AND d.deployment_id=r.deployment_id WHERE r.tenant_id=? AND r.environment=? AND r.run_id=?
                    """,scope.tenant(),scope.environment(),lease.runId());
            var model=models.runtimeVersionForAuthorizedLease(scope.tenant(),scope.environment(),
                    (String)row.get("model_connection_id"),((Number)row.get("model_version")).longValue());
            var skills=jdbc.query("SELECT release_id,manifest_sha256 FROM oa_agent_deployment_skill "
                    + "WHERE tenant_id=? AND environment=? AND deployment_id=? ORDER BY release_id",
                    (rs,index)->new SkillRef(rs.getString(1),rs.getString(2)),scope.tenant(),scope.environment(),row.get("deployment_id"));
            try {
                return new RuntimeBundle(json.readTree((String)row.get("input_json")), json.readTree((String)row.get("snapshot_json")),
                        model, ((Timestamp)row.get("deadline_at")).toInstant().toString(), List.copyOf(skills));
            } catch (java.io.IOException invalid) { throw new RunFailure("execution_configuration_invalid"); }
        });
    }

    public byte[] artifact(RunLedger.Lease lease, String releaseId, int fileIndex) {
        if (fileIndex < -1 || fileIndex > 99) throw new RunFailure("invalid_artifact_index");
        // External object-store I/O is outside the database transaction. Recheck the
        // lease after the bounded read, so cancellation/revocation during I/O fails closed.
        var metadata=ledger.withPermit(lease, () -> {
            var scope=lease.scope();
            var rows=jdbc.queryForList("""
                    SELECT s.manifest_json,s.manifest_sha256 FROM oa_agent_run r
                    JOIN oa_agent_deployment_skill b ON b.tenant_id=r.tenant_id AND b.environment=r.environment AND b.deployment_id=r.deployment_id
                    JOIN oa_skill_release s ON s.tenant_id=b.tenant_id AND s.release_id=b.release_id
                    WHERE r.tenant_id=? AND r.environment=? AND r.run_id=? AND b.release_id=?
                    AND s.status='ready' AND s.manifest_sha256=b.manifest_sha256
                    """,scope.tenant(),scope.environment(),lease.runId(),releaseId);
            if(rows.isEmpty()) throw new RunFailure("resource_unavailable");
            if(fileIndex==-1) return rows.getFirst();
            var files=jdbc.queryForList("SELECT object_key,size_bytes,content_sha256 FROM oa_skill_release_file WHERE tenant_id=? AND release_id=? AND file_index=?",
                    scope.tenant(),releaseId,fileIndex);
            if(files.isEmpty()) throw new RunFailure("resource_unavailable");
            return files.getFirst();
        });
        byte[] content;
        if(fileIndex==-1) {
            String manifest=(String)metadata.get("manifest_json");
            if(!RunJson.hash(manifest).equals(metadata.get("manifest_sha256"))) throw new RunFailure("artifact_integrity_failed");
            content=manifest.getBytes(StandardCharsets.UTF_8);
            if(content.length>524288) throw new RunFailure("artifact_limit");
        } else {
            content=artifacts.get((String)metadata.get("object_key"),((Number)metadata.get("size_bytes")).intValue(),(String)metadata.get("content_sha256"));
        }
        return ledger.withPermit(lease,()->content);
    }
}
