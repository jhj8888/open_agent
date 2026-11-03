package com.iflytek.astron.console.toolkit.security;

import static org.assertj.core.api.Assertions.assertThatCode;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import com.iflytek.astron.console.toolkit.config.properties.SandboxRuntimeCredentialProperties;
import com.iflytek.astron.console.toolkit.config.properties.SkillSandboxArtifactProperties;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

class ServiceCredentialIsolationGuardTest {

    private static final String ARTIFACT_TOKEN =
            "artifact-token-that-must-never-appear-in-an-error-0123456789";
    private static final String RUNTIME_TOKEN =
            "runtime-token-that-must-never-appear-in-an-error-0123456789";

    @TempDir
    Path tempDirectory;

    @Test
    void acceptsDistinctInitializedCredentials() {
        ArtifactUploadTokenProvider artifactProvider = artifactProvider(ARTIFACT_TOKEN, null);
        SandboxRuntimeCredentialTokenProvider runtimeProvider =
                runtimeProvider(RUNTIME_TOKEN, null);
        artifactProvider.initialize();
        runtimeProvider.initialize();

        ServiceCredentialIsolationGuard guard =
                new ServiceCredentialIsolationGuard(artifactProvider, runtimeProvider);

        assertThatCode(guard::afterSingletonsInstantiated).doesNotThrowAnyException();
    }

    @Test
    void rejectsSameExplicitCredentialWithoutExposingIt() {
        ArtifactUploadTokenProvider artifactProvider = artifactProvider(ARTIFACT_TOKEN, null);
        SandboxRuntimeCredentialTokenProvider runtimeProvider =
                runtimeProvider(ARTIFACT_TOKEN, null);
        artifactProvider.initialize();
        runtimeProvider.initialize();

        ServiceCredentialIsolationGuard guard =
                new ServiceCredentialIsolationGuard(artifactProvider, runtimeProvider);

        assertThatThrownBy(guard::afterSingletonsInstantiated)
                .isInstanceOf(IllegalStateException.class)
                .hasMessage("Workflow artifact upload and sandbox runtime credentials must be different")
                .hasMessageNotContaining(ARTIFACT_TOKEN);
    }

    @Test
    void rejectsSameFinalCredentialGeneratedInSharedFile() throws IOException {
        Path sharedPath = tempDirectory.resolve("shared/token");
        ArtifactUploadTokenProvider artifactProvider = artifactProvider(null, sharedPath);
        SandboxRuntimeCredentialTokenProvider runtimeProvider = runtimeProvider(null, sharedPath);
        artifactProvider.initialize();
        runtimeProvider.initialize();
        String generatedToken = Files.readString(sharedPath, StandardCharsets.UTF_8).trim();

        ServiceCredentialIsolationGuard guard =
                new ServiceCredentialIsolationGuard(artifactProvider, runtimeProvider);

        assertThatThrownBy(guard::afterSingletonsInstantiated)
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("credentials must be different")
                .hasMessageNotContaining(generatedToken);
    }

    private ArtifactUploadTokenProvider artifactProvider(String token, Path tokenPath) {
        SkillSandboxArtifactProperties properties = new SkillSandboxArtifactProperties();
        properties.setArtifactUploadToken(token);
        properties.setArtifactUploadTokenFile(tokenPath == null ? null : tokenPath.toString());
        return new ArtifactUploadTokenProvider(properties);
    }

    private SandboxRuntimeCredentialTokenProvider runtimeProvider(String token, Path tokenPath) {
        SandboxRuntimeCredentialProperties properties = new SandboxRuntimeCredentialProperties();
        properties.setToken(token);
        properties.setTokenFile(tokenPath == null ? null : tokenPath.toString());
        return new SandboxRuntimeCredentialTokenProvider(properties);
    }
}
