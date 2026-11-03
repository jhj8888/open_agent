package com.iflytek.astron.console.hub.platform.skill;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.iflytek.astron.console.commons.tenant.*;
import com.iflytek.astron.console.commons.util.RequestContextUtil;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.*;
import java.util.concurrent.Semaphore;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.core.env.Environment;
import org.springframework.dao.OptimisticLockingFailureException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.support.TransactionTemplate;

/** Browser -> tenant authorization -> bounded ZIP parser -> private S3 -> atomic DB publication.
 * No archive extraction, package installation or tenant code execution occurs here. */
@Service
public class SkillReleaseService {
    private static final Logger log=LoggerFactory.getLogger(SkillReleaseService.class);
    private static final String PUBLIC="r.release_id,r.skill_name,r.description,r.version_label,r.manifest_sha256,r.runtime_image,r.total_bytes,r.file_count,r.status,r.source_kind,r.legacy_skill_id,r.created_at,r.retain_until";
    private final JdbcTemplate jdbc;
    private final TenantAccessService access;
    private final SkillArtifactStore store;
    private final TransactionTemplate tx;
    private final ObjectMapper json;
    private final Environment env;
    private final Semaphore uploads=new Semaphore(1);

    public SkillReleaseService(JdbcTemplate jdbc,TenantAccessService access,SkillArtifactStore store,
            PlatformTransactionManager transactions,ObjectMapper json,Environment env) {
        this.jdbc=jdbc;this.access=access;this.store=store;this.tx=new TransactionTemplate(transactions);this.json=json;this.env=env;
    }
    public List<String> images(String tenant,String environment) {
        access.require(tenant,environment,TenantPermission.METADATA_READ);
        try {
            List<String> values=json.readValue(env.getProperty("SKILL_RUNTIME_IMAGES","[]"),new TypeReference<>() {});
            if(values.size()>50 || values.stream().anyMatch(value->value==null || value.length()>512 || !value.matches("[a-z0-9][a-z0-9._:/-]*@sha256:[a-f0-9]{64}")))
                throw new IllegalArgumentException();
            return List.copyOf(values);
        } catch(Exception exception) { throw new IllegalStateException("Invalid configured Skill runtime images"); }
    }
    public List<Map<String,Object>> list(String tenant,String environment) {
        access.require(tenant,environment,TenantPermission.METADATA_READ);
        return jdbc.queryForList("SELECT "+PUBLIC+",EXISTS(SELECT 1 FROM oa_skill_verification v WHERE v.tenant_id=r.tenant_id AND v.release_id=r.release_id AND v.passed=true AND v.manifest_sha256=r.manifest_sha256) AS verified "
                +"FROM oa_skill_release r JOIN oa_skill_release_environment e ON e.tenant_id=r.tenant_id AND e.release_id=r.release_id WHERE e.tenant_id=? AND e.environment=? ORDER BY r.created_at DESC LIMIT 500",tenant,environment);
    }
    public Map<String,Object> publish(String tenant,String environment,String version,String image,InputStream archive) throws IOException {
        requirePublication(tenant,environment,version,image);
        if(!uploads.tryAcquire()) throw new ReleaseConflictException("Another Skill upload is in progress; retry shortly");
        Path temporary=null;
        try {
            temporary=Files.createTempFile("open-agent-skill-",".zip");
            try(var output=Files.newOutputStream(temporary)) {
                byte[] buffer=new byte[8192]; int count,total=0;
                while((count=archive.read(buffer))!=-1) {
                    total+=count;
                    if(total>SkillArchiveValidator.MAX_ARCHIVE_BYTES) throw new IllegalArgumentException("Skill archive exceeds 8 MiB");
                    output.write(buffer,0,count);
                }
            }
            SkillArchiveValidator.Validated validated;
            try { validated=new SkillArchiveValidator().validate(temporary); }
            catch(IOException exception) { throw new IllegalArgumentException("Invalid ZIP archive or SKILL.md encoding"); }
            return publishValidated(tenant,environment,version,image,validated,null);
        } finally {
            try { if(temporary!=null) Files.deleteIfExists(temporary); } finally { uploads.release(); }
        }
    }
    private void requirePublication(String tenant,String environment,String version,String image) {
        access.require(tenant,environment,TenantPermission.RESOURCE_WRITE);
        if(version==null || !version.matches("[A-Za-z0-9][A-Za-z0-9._-]{0,63}")) throw new IllegalArgumentException("Invalid release version");
        if(!images(tenant,environment).contains(image)) throw new IllegalArgumentException("Runtime image is not approved");
    }
    Map<String,Object> publishLegacy(String tenant,String environment,String version,String image,long legacyId,
            java.util.function.Supplier<SkillArchiveValidator.Validated> snapshot) {
        requirePublication(tenant,environment,version,image);
        if(!uploads.tryAcquire()) throw new ReleaseConflictException("Another Skill upload is in progress; retry shortly");
        try {
            return publishValidated(tenant,environment,version,image,snapshot.get(),legacyId);
        } finally { uploads.release(); }
    }
    Map<String,Object> publishValidated(String tenant,String environment,String version,String image,SkillArchiveValidator.Validated skill,Long legacyId) {
        String upload=UUID.randomUUID().toString();
        String prefix=SkillArtifactStore.prefix(tenant,upload);
        var manifest=SkillReleaseManifest.encode(json,skill,version,image);
        tx.executeWithoutResult(status->{
            lockTenant(tenant);access.require(tenant,environment,TenantPermission.RESOURCE_WRITE);
            jdbc.update("INSERT INTO oa_skill_upload(tenant_id,environment,upload_id,object_prefix,expires_at) VALUES(?,?,?,?,TIMESTAMPADD(MINUTE,10,CURRENT_TIMESTAMP(3)))",tenant,environment,upload,prefix);
        });
        try {
            // Lock only the private staging row during I/O; GC cannot delete an active writer's objects.
            tx.executeWithoutResult(status->{
                if(jdbc.queryForList("SELECT upload_id FROM oa_skill_upload WHERE tenant_id=? AND upload_id=? AND state='uploading' AND expires_at>CURRENT_TIMESTAMP(3) FOR UPDATE",tenant,upload).isEmpty())
                    throw new ReleaseConflictException("Skill upload expired");
                long deadline=System.nanoTime()+java.time.Duration.ofMinutes(3).toNanos();
                for(int i=0;i<skill.files().size();i++) {
                    if(System.nanoTime()>deadline) throw new ReleaseConflictException("Skill upload timed out");
                    var file=skill.files().get(i);store.put(prefix+i+"-"+file.sha256(),file.bytes());
                }
            });
            Map<String,Object> result=tx.execute(status->{
                lockTenant(tenant);access.require(tenant,environment,TenantPermission.RESOURCE_WRITE);
                var stage=jdbc.queryForList("SELECT upload_id FROM oa_skill_upload WHERE tenant_id=? AND upload_id=? AND state='uploading' AND expires_at>CURRENT_TIMESTAMP(3) FOR UPDATE",tenant,upload);
                if(stage.isEmpty()) throw new ReleaseConflictException("Skill upload expired");
                var existing=jdbc.queryForList("SELECT release_id,manifest_sha256,status FROM oa_skill_release WHERE tenant_id=? AND skill_name=? AND version_label=?",tenant,skill.name(),version);
                if(!existing.isEmpty()) {
                    var old=existing.getFirst();
                    if(!manifest.sha256().equals(old.get("manifest_sha256")) || !"ready".equals(old.get("status"))) throw new ReleaseConflictException("This Skill version already exists with different content or state");
                    jdbc.update("UPDATE oa_skill_upload SET state='failed' WHERE tenant_id=? AND upload_id=?",tenant,upload);
                    return Map.of("releaseId",old.get("release_id"),"manifestSha256",manifest.sha256(),"reused",true);
                }
                jdbc.update("""
                        INSERT INTO oa_skill_release(tenant_id,release_id,skill_name,description,version_label,source_kind,legacy_skill_id,
                        manifest_sha256,manifest_json,runtime_image,object_prefix,total_bytes,file_count,created_by,retain_until)
                        SELECT ?,?,?,?,?,?,?,?,?,?,?,?,?,?,TIMESTAMPADD(DAY,audit_retention_days,CURRENT_TIMESTAMP(3))
                        FROM oa_tenant_environment WHERE tenant_id=? AND environment=?
                        """,tenant,upload,skill.name(),skill.description(),version,legacyId==null?"upload":"legacy-import",legacyId,
                        manifest.sha256(),manifest.json(),image,prefix,skill.totalBytes(),skill.files().size(),RequestContextUtil.getUID(),tenant,environment);
                for(int i=0;i<skill.files().size();i++) {
                    var file=skill.files().get(i);
                    jdbc.update("INSERT INTO oa_skill_release_file(tenant_id,release_id,file_index,file_path,path_sha256,content_sha256,size_bytes,object_key) VALUES(?,?,?,?,?,?,?,?)",
                            tenant,upload,i,file.path(),SkillArchiveValidator.sha256(file.path().getBytes(StandardCharsets.UTF_8)),file.sha256(),file.bytes().length,prefix+i+"-"+file.sha256());
                }
                grant(tenant,environment,upload);
                jdbc.update("UPDATE oa_skill_upload SET state='published' WHERE tenant_id=? AND upload_id=?",tenant,upload);
                audit(tenant,environment,upload,"skill.release.created",manifest.sha256());
                return Map.of("releaseId",upload,"manifestSha256",manifest.sha256(),"reused",false);
            });
            if(Boolean.TRUE.equals(result.get("reused"))) {
                try { cleanOne(tenant,upload); }
                catch(RuntimeException cleanup) { log.warn("Duplicate Skill staging cleanup deferred tenant={} upload={}",tenant,upload); }
            }
            log.info("Skill release published tenant={} release={} manifest={}",tenant,result.get("releaseId"),manifest.sha256());
            return result;
        } catch(RuntimeException exception) {
            // Never delete objects whose publication committed, even after an ambiguous commit response.
            try {
                jdbc.update("UPDATE oa_skill_upload SET state='failed' WHERE tenant_id=? AND upload_id=? AND state='uploading'",tenant,upload);
                cleanOne(tenant,upload);
            } catch(RuntimeException cleanup) { log.warn("Skill staging cleanup deferred tenant={} upload={}",tenant,upload); }
            throw exception;
        }
    }
    public String manifest(String tenant,String environment,String release) {
        scoped(tenant,environment,release);
        var row=jdbc.queryForMap("SELECT manifest_json,manifest_sha256 FROM oa_skill_release WHERE tenant_id=? AND release_id=?",tenant,release);
        String value=(String)row.get("manifest_json");
        if(!SkillArchiveValidator.sha256(value.getBytes(StandardCharsets.UTF_8)).equals(row.get("manifest_sha256"))) throw new MinioSkillArtifactStore.ArtifactUnavailableException();
        return value;
    }
    public byte[] download(String tenant,String environment,String release,int index) {
        scoped(tenant,environment,release);
        var rows=jdbc.queryForList("SELECT object_key,size_bytes,content_sha256 FROM oa_skill_release_file WHERE tenant_id=? AND release_id=? AND file_index=?",tenant,release,index);
        if(rows.isEmpty()) throw new TenantAccessDeniedException();
        var file=rows.getFirst();
        byte[] bytes=store.get((String)file.get("object_key"),((Number)file.get("size_bytes")).intValue(),(String)file.get("content_sha256"));
        // Membership may be removed while object storage is responding.
        access.require(tenant,environment,TenantPermission.METADATA_READ);
        return bytes;
    }
    public void promote(String tenant,String release) {
        tx.executeWithoutResult(status->{
            lockTenant(tenant);access.require(tenant,"prod",TenantPermission.DEPLOYMENT_PUBLISH);
            var rows=jdbc.queryForList("""
                    SELECT r.manifest_sha256 FROM oa_skill_release r JOIN oa_skill_release_environment e
                    ON e.tenant_id=r.tenant_id AND e.release_id=r.release_id AND e.environment='test'
                    WHERE r.tenant_id=? AND r.release_id=? AND r.status='ready' AND EXISTS
                    (SELECT 1 FROM oa_skill_verification v WHERE v.tenant_id=r.tenant_id AND v.release_id=r.release_id AND v.passed=true AND v.manifest_sha256=r.manifest_sha256)
                    """,tenant,release);
            if(rows.isEmpty()) throw new ReleaseConflictException("An available release with successful test evidence is required");
            grant(tenant,"prod",release);audit(tenant,"prod",release,"skill.release.promoted",(String)rows.getFirst().get("manifest_sha256"));
        });
    }
    public void delete(String tenant,String environment,String release) {
        tx.executeWithoutResult(status->{
            lockTenant(tenant);access.require(tenant,environment,TenantPermission.RESOURCE_WRITE);
            scoped(tenant,environment,release);
            int changed=jdbc.update("""
                    UPDATE oa_skill_release r SET status='deleted' WHERE tenant_id=? AND release_id=? AND retain_until<CURRENT_TIMESTAMP(3)
                    AND NOT EXISTS(SELECT 1 FROM oa_skill_release_reference x WHERE x.tenant_id=r.tenant_id AND x.release_id=r.release_id
                        AND (x.retain_until IS NULL OR x.retain_until>=CURRENT_TIMESTAMP(3)))
                    AND NOT EXISTS(SELECT 1 FROM oa_skill_release_environment e WHERE e.tenant_id=r.tenant_id AND e.release_id=r.release_id AND e.environment='prod')
                    """,tenant,release);
            if(changed!=1) throw new ReleaseConflictException("Release is retained, referenced by a task/deployment or promoted to production");
            jdbc.update("UPDATE oa_skill_upload SET state='failed' WHERE tenant_id=? AND upload_id=? AND state='published'",tenant,release);
            audit(tenant,environment,release,"skill.release.deleted",null);
        });
        cleanOne(tenant,release);
    }
    private void scoped(String tenant,String environment,String release) {
        access.require(tenant,environment,TenantPermission.METADATA_READ);
        if(jdbc.queryForList("SELECT r.release_id FROM oa_skill_release r JOIN oa_skill_release_environment e ON e.tenant_id=r.tenant_id AND e.release_id=r.release_id WHERE e.tenant_id=? AND e.environment=? AND e.release_id=? AND r.status<>'deleted'",tenant,environment,release).isEmpty())
            throw new TenantAccessDeniedException();
    }
    private void lockTenant(String tenant) {
        if(jdbc.queryForList("SELECT tenant_id FROM oa_tenant WHERE tenant_id=? FOR UPDATE",tenant).isEmpty()) throw new TenantAccessDeniedException();
    }
    private void grant(String tenant,String environment,String release) {
        jdbc.update("INSERT INTO oa_skill_release_environment(tenant_id,environment,release_id,granted_by) VALUES(?,?,?,?) ON DUPLICATE KEY UPDATE release_id=VALUES(release_id)",tenant,environment,release,RequestContextUtil.getUID());
    }
    private void audit(String tenant,String environment,String release,String action,String digest) {
        jdbc.update("INSERT INTO oa_tenant_audit(tenant_id,environment,actor_uid,action,subject_id,after_value,retain_until) SELECT tenant_id,environment,?,?,?,JSON_OBJECT('manifestSha256',?),TIMESTAMPADD(DAY,audit_retention_days,CURRENT_TIMESTAMP(3)) FROM oa_tenant_environment WHERE tenant_id=? AND environment=?",
                RequestContextUtil.getUID(),action,release,digest,tenant,environment);
    }
    @Scheduled(fixedDelay=600000,initialDelay=600000)
    public void cleanStaging() {
        for(var row:jdbc.queryForList("SELECT tenant_id,upload_id FROM oa_skill_upload WHERE state='failed' OR (state='uploading' AND expires_at<CURRENT_TIMESTAMP(3)) ORDER BY created_at LIMIT 20")) {
            try { cleanOne((String)row.get("tenant_id"),(String)row.get("upload_id")); }
            catch(RuntimeException exception) { log.warn("Skill staging cleanup deferred upload={}",row.get("upload_id")); }
        }
    }
    private void cleanOne(String tenant,String upload) {
        tx.executeWithoutResult(status->{
            var rows=jdbc.queryForList("SELECT object_prefix FROM oa_skill_upload WHERE tenant_id=? AND upload_id=? AND (state='failed' OR (state='uploading' AND expires_at<CURRENT_TIMESTAMP(3))) FOR UPDATE SKIP LOCKED",tenant,upload);
            if(rows.isEmpty()) return;
            if(!jdbc.queryForList("SELECT release_id FROM oa_skill_release WHERE tenant_id=? AND release_id=? AND status<>'deleted'",tenant,upload).isEmpty()) return;
            String prefix=(String)rows.getFirst().get("object_prefix");
            if(!SkillArtifactStore.prefix(tenant,upload).equals(prefix)) throw new IllegalStateException("Invalid staging prefix");
            store.removePrefix(prefix);
            jdbc.update("UPDATE oa_skill_upload SET state='cleaned' WHERE tenant_id=? AND upload_id=?",tenant,upload);
        });
    }
    public static final class ReleaseConflictException extends OptimisticLockingFailureException {
        public ReleaseConflictException(String message) { super(message); }
    }
}
