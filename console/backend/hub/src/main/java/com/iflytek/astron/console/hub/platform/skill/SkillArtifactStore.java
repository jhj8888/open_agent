package com.iflytek.astron.console.hub.platform.skill;

import java.nio.charset.StandardCharsets;
import java.util.UUID;

/** Private objects only; no public URLs or user-controlled object keys. */
public interface SkillArtifactStore {
    String ROOT = "workflow/artifacts/open-agent/skill-releases/";
    void put(String key, byte[] content);
    byte[] get(String key, int expectedSize, String expectedDigest);
    void removePrefix(String prefix);

    static String prefix(String tenant, String uploadId) {
        return ROOT + SkillArchiveValidator.sha256(tenant.getBytes(StandardCharsets.UTF_8)) + "/" + UUID.fromString(uploadId) + "/";
    }
    static void checkPrefix(String prefix) {
        if(prefix==null || !prefix.matches(ROOT + "[a-f0-9]{64}/[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}/"))
            throw new IllegalArgumentException("Invalid artifact prefix");
    }
    static void checkKey(String key) {
        if(key==null || key.lastIndexOf('/')<0) throw new IllegalArgumentException("Invalid artifact key");
        checkPrefix(key.substring(0,key.lastIndexOf('/')+1));
        if(!key.substring(key.lastIndexOf('/')+1).matches("[0-9]{1,3}-[a-f0-9]{64}")) throw new IllegalArgumentException("Invalid artifact key");
    }
}
