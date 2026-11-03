package com.iflytek.astron.console.hub.platform.skill;

import com.iflytek.astron.console.commons.response.ApiResult;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import org.springframework.http.*;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.multipart.MultipartFile;

@RestController
@RequestMapping("/platform/tenants/{tenantId}/environments/{environment}/skill-releases")
public class PlatformSkillController {
    private final SkillReleaseService service;
    private final LegacySkillReleaseImporter importer;
    public PlatformSkillController(SkillReleaseService service,LegacySkillReleaseImporter importer) { this.service=service;this.importer=importer; }
    public record ImportDraft(long skillId,String version,String runtimeImage) {}
    @PostMapping("/import-draft") public ApiResult<?> importDraft(@PathVariable String tenantId,@PathVariable String environment,@RequestBody ImportDraft input) {
        return ApiResult.success(importer.importDraft(tenantId,environment,input.skillId(),input.version(),input.runtimeImage()));
    }
    @GetMapping public ApiResult<?> list(@PathVariable String tenantId,@PathVariable String environment) {
        return ApiResult.success(service.list(tenantId,environment));
    }
    @GetMapping("/runtime-images") public ApiResult<?> images(@PathVariable String tenantId,@PathVariable String environment) {
        return ApiResult.success(service.images(tenantId,environment));
    }
    @PostMapping(consumes=MediaType.MULTIPART_FORM_DATA_VALUE)
    public ApiResult<?> publish(@PathVariable String tenantId,@PathVariable String environment,@RequestParam String version,
            @RequestParam String runtimeImage,@RequestPart("archive") MultipartFile archive) throws IOException {
        if(archive.getSize()>SkillArchiveValidator.MAX_ARCHIVE_BYTES) throw new IllegalArgumentException("Skill archive exceeds 8 MiB");
        try(var input=archive.getInputStream()) { return ApiResult.success(service.publish(tenantId,environment,version,runtimeImage,input)); }
    }
    @GetMapping("/{releaseId}/manifest") public ResponseEntity<byte[]> manifest(@PathVariable String tenantId,@PathVariable String environment,@PathVariable String releaseId) {
        byte[] bytes=service.manifest(tenantId,environment,releaseId).getBytes(StandardCharsets.UTF_8);
        return ResponseEntity.ok().contentType(MediaType.APPLICATION_JSON).cacheControl(CacheControl.noStore())
                .header("X-Manifest-SHA256",SkillArchiveValidator.sha256(bytes)).header("X-Content-Type-Options","nosniff").body(bytes);
    }
    @GetMapping("/{releaseId}/files/{index}") public ResponseEntity<byte[]> file(@PathVariable String tenantId,@PathVariable String environment,@PathVariable String releaseId,@PathVariable int index) {
        return ResponseEntity.ok().contentType(MediaType.APPLICATION_OCTET_STREAM).cacheControl(CacheControl.noStore())
                .header("Content-Disposition","attachment; filename=skill-file").header("X-Content-Type-Options","nosniff")
                .body(service.download(tenantId,environment,releaseId,index));
    }
    @PostMapping("/{releaseId}/promote") public ApiResult<?> promote(@PathVariable String tenantId,@PathVariable String environment,@PathVariable String releaseId) {
        if(!"prod".equals(environment)) throw new IllegalArgumentException("Promotion requires the production environment");
        service.promote(tenantId,releaseId);return ApiResult.success();
    }
    @DeleteMapping("/{releaseId}") public ApiResult<?> delete(@PathVariable String tenantId,@PathVariable String environment,@PathVariable String releaseId) {
        service.delete(tenantId,environment,releaseId);return ApiResult.success();
    }
}
