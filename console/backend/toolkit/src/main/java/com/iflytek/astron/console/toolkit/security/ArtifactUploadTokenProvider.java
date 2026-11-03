package com.iflytek.astron.console.toolkit.security;

import com.iflytek.astron.console.toolkit.config.properties.SkillSandboxArtifactProperties;
import jakarta.annotation.PostConstruct;
import java.nio.file.Path;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.apache.commons.lang3.StringUtils;
import org.springframework.stereotype.Component;

/** Compares the dedicated artifact-upload credential without exposing its plaintext value. */
@Component
@Slf4j
@RequiredArgsConstructor
public class ArtifactUploadTokenProvider {

    private final SkillSandboxArtifactProperties properties;
    private byte[] tokenBytes;

    @PostConstruct
    void initialize() {
        String tokenFile = StringUtils.trimToEmpty(properties.getArtifactUploadTokenFile());
        tokenBytes = PersistentServiceToken.loadOrCreate(
                properties.getArtifactUploadToken(),
                StringUtils.isBlank(tokenFile) ? null : Path.of(tokenFile),
                "Workflow artifact upload");
        log.info("Workflow artifact upload credential initialized");
    }

    public boolean matches(String candidate) {
        return PersistentServiceToken.matches(tokenBytes, candidate);
    }

    boolean usesSameCredentialAs(SandboxRuntimeCredentialTokenProvider otherProvider) {
        return otherProvider.matchesCredentialBytes(tokenBytes);
    }
}
