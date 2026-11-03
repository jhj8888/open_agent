package com.iflytek.astron.console.toolkit.security;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import com.iflytek.astron.console.toolkit.config.properties.SandboxRuntimeCredentialProperties;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

class SandboxRuntimeCredentialTokenProviderTest {

    private static final String VALID_TOKEN =
            "astron-runtime-credential-token-0123456789-ABCDEFGHIJKLMNOPQRSTUVWXYZ";

    @TempDir
    Path tempDirectory;

    @Test
    void initializeMatchesOnlyTheConfiguredCredential() {
        SandboxRuntimeCredentialProperties properties = new SandboxRuntimeCredentialProperties();
        properties.setToken(VALID_TOKEN);
        SandboxRuntimeCredentialTokenProvider provider =
                new SandboxRuntimeCredentialTokenProvider(properties);

        provider.initialize();

        assertThat(provider.matches(VALID_TOKEN)).isTrue();
        assertThat(provider.matches(VALID_TOKEN + " ")).isFalse();
        assertThat(provider.matches(null)).isFalse();
        assertThat(provider.signExecutionRequest(1_700_000_000L, "{\"command\":\"echo hi\"}"))
                .isEqualTo("ab262163275b2152bf02c0937c63f5b98eb06041e1c356bb7c817e7c5072fdda");
    }

    @Test
    void initializeGeneratesAndReusesPersistentCredential() throws IOException {
        Path tokenPath = tempDirectory.resolve("secrets/runtime-credential-token");
        SandboxRuntimeCredentialProperties properties = fileProperties(tokenPath);
        SandboxRuntimeCredentialTokenProvider first =
                new SandboxRuntimeCredentialTokenProvider(properties);

        first.initialize();
        String generated = Files.readString(tokenPath, StandardCharsets.UTF_8).trim();

        SandboxRuntimeCredentialTokenProvider second =
                new SandboxRuntimeCredentialTokenProvider(properties);
        second.initialize();
        assertThat(generated).hasSize(64).matches("[A-Za-z0-9_-]{64}");
        assertThat(second.matches(generated)).isTrue();
    }

    @Test
    void explicitCredentialReplacesRetiredFallbackFile() throws IOException {
        Path tokenPath = tempDirectory.resolve("secrets/runtime-credential-token");
        Files.createDirectories(tokenPath.getParent());
        Files.writeString(
                tokenPath,
                "retired-runtime-credential-token-0123456789-ABCDEFGHIJKLMNOPQRSTUVWXYZ\n",
                StandardCharsets.UTF_8);
        SandboxRuntimeCredentialProperties properties = new SandboxRuntimeCredentialProperties();
        properties.setToken(VALID_TOKEN);
        properties.setTokenFile(tokenPath.toString());

        new SandboxRuntimeCredentialTokenProvider(properties).initialize();

        assertThat(Files.readString(tokenPath, StandardCharsets.UTF_8).trim())
                .isEqualTo(VALID_TOKEN);
        SandboxRuntimeCredentialTokenProvider fallback =
                new SandboxRuntimeCredentialTokenProvider(fileProperties(tokenPath));
        fallback.initialize();
        assertThat(fallback.matches(VALID_TOKEN)).isTrue();
    }

    @Test
    void initializeRejectsMissingOrShortCredential() {
        SandboxRuntimeCredentialProperties missing = new SandboxRuntimeCredentialProperties();
        SandboxRuntimeCredentialProperties shortCredential =
                new SandboxRuntimeCredentialProperties();
        shortCredential.setToken("too-short");

        assertThatThrownBy(
                () -> new SandboxRuntimeCredentialTokenProvider(missing).initialize())
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("not configured");
        assertThatThrownBy(() -> new SandboxRuntimeCredentialTokenProvider(shortCredential)
                .initialize())
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("at least 32 characters");
    }

    @Test
    void initializeRejectsCredentialContainingLineBreaks() {
        SandboxRuntimeCredentialProperties properties = new SandboxRuntimeCredentialProperties();
        properties.setToken(VALID_TOKEN + "\r\n");

        assertThatThrownBy(() -> new SandboxRuntimeCredentialTokenProvider(properties)
                .initialize())
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("must not contain line breaks");
    }

    private SandboxRuntimeCredentialProperties fileProperties(Path tokenPath) {
        SandboxRuntimeCredentialProperties properties = new SandboxRuntimeCredentialProperties();
        properties.setTokenFile(tokenPath.toString());
        return properties;
    }
}
