package com.iflytek.astron.console.hub.platform.skill;

import com.fasterxml.jackson.databind.ObjectMapper;
import java.nio.charset.StandardCharsets;
import java.util.List;

/** Persist these exact UTF-8 bytes. Consumers hash the bytes before parsing, never reserialize. */
public record SkillReleaseManifest(int schemaVersion,String name,String version,String runtimeImage,
        String dependencyPolicy,List<File> files) {
    public record File(String path,long size,String sha256,boolean dependencyLock) {}
    public record Encoded(String json,String sha256) {}
    public static Encoded encode(ObjectMapper json,SkillArchiveValidator.Validated skill,String version,String image) {
        var files=skill.files().stream().map(file->new File(file.path(),file.bytes().length,file.sha256(),
                file.path().matches("(?:.*/)?(?:uv\\.lock|poetry\\.lock|requirements[^/]*\\.txt|package-lock\\.json|pnpm-lock\\.yaml|yarn\\.lock)"))).toList();
        try {
            String value=json.writeValueAsString(new SkillReleaseManifest(1,skill.name(),version,image,"preinstalled-image-only",files));
            return new Encoded(value,SkillArchiveValidator.sha256(value.getBytes(StandardCharsets.UTF_8)));
        } catch(Exception exception) { throw new IllegalStateException("Cannot encode Skill manifest"); }
    }
}
