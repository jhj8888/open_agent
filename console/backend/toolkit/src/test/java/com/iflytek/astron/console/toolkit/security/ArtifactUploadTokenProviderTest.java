package com.iflytek.astron.console.toolkit.security;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import com.iflytek.astron.console.toolkit.config.properties.SkillSandboxArtifactProperties;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.attribute.PosixFileAttributeView;
import java.nio.file.attribute.PosixFilePermission;
import java.util.EnumSet;
import java.util.Set;
import org.junit.jupiter.api.Assumptions;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

class ArtifactUploadTokenProviderTest {

    private static final String VALID_TOKEN =
            "astron-artifact-upload-token-0123456789-ABCDEFGHIJKLMNOPQRSTUVWXYZ";

    @TempDir
    Path tempDirectory;

    @Test
    void initializeUsesTrimmedExplicitTokenAndMatchesOnlyExactValue() {
        SkillSandboxArtifactProperties properties = new SkillSandboxArtifactProperties();
        properties.setArtifactUploadToken(" " + VALID_TOKEN + "\t ");
        ArtifactUploadTokenProvider provider = new ArtifactUploadTokenProvider(properties);

        provider.initialize();

        assertThat(provider.matches(VALID_TOKEN)).isTrue();
        assertThat(provider.matches(VALID_TOKEN + " ")).isFalse();
        assertThat(provider.matches("different-token-with-more-than-thirty-two-characters")).isFalse();
        assertThat(provider.matches(null)).isFalse();
    }

    @Test
    void explicitTokenAtomicallyReplacesStaleFallbackFile() throws IOException {
        String retiredToken = "retired-artifact-upload-token-0123456789-ABCDEFGHIJKLMNOPQRSTUVWXYZ";
        Path tokenPath = tempDirectory.resolve("secrets/artifact-upload-token");
        Files.createDirectories(tokenPath.getParent());
        Files.writeString(tokenPath, retiredToken + System.lineSeparator(), StandardCharsets.UTF_8);
        SkillSandboxArtifactProperties properties = new SkillSandboxArtifactProperties();
        properties.setArtifactUploadToken(VALID_TOKEN);
        properties.setArtifactUploadTokenFile(tokenPath.toString());

        new ArtifactUploadTokenProvider(properties).initialize();

        assertThat(Files.readString(tokenPath, StandardCharsets.UTF_8).trim())
                .isEqualTo(VALID_TOKEN);
        ArtifactUploadTokenProvider fallbackProvider =
                new ArtifactUploadTokenProvider(fileProperties(tokenPath));
        fallbackProvider.initialize();
        assertThat(fallbackProvider.matches(VALID_TOKEN)).isTrue();
        assertThat(fallbackProvider.matches(retiredToken)).isFalse();
        assertOwnerOnlyPermissionsIfSupported(tokenPath, false);
    }

    @Test
    void initializeRejectsShortExplicitToken() {
        SkillSandboxArtifactProperties properties = new SkillSandboxArtifactProperties();
        properties.setArtifactUploadToken("too-short");
        ArtifactUploadTokenProvider provider = new ArtifactUploadTokenProvider(properties);

        assertThatThrownBy(provider::initialize)
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("configured value")
                .hasMessageContaining("at least 32 characters");
    }

    @Test
    void initializeRejectsExplicitTokenContainingLineBreaks() {
        SkillSandboxArtifactProperties properties = new SkillSandboxArtifactProperties();
        properties.setArtifactUploadToken(VALID_TOKEN + "\n");

        assertThatThrownBy(() -> new ArtifactUploadTokenProvider(properties).initialize())
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("must not contain line breaks");
    }

    @Test
    void initializeGeneratesPersistentTokenAndReusesItAcrossProviders() throws IOException {
        Path tokenPath = tempDirectory.resolve("nested/secrets/artifact-upload-token");
        SkillSandboxArtifactProperties properties = fileProperties(tokenPath);
        ArtifactUploadTokenProvider firstProvider = new ArtifactUploadTokenProvider(properties);

        firstProvider.initialize();

        String generatedToken = Files.readString(tokenPath, StandardCharsets.UTF_8).trim();
        assertThat(generatedToken).hasSize(64).matches("[A-Za-z0-9_-]{64}");
        assertThat(Files.readString(tokenPath, StandardCharsets.UTF_8).trim())
                .isEqualTo(generatedToken);
        assertThat(tokenPath).isRegularFile();
        assertOwnerOnlyPermissionsIfSupported(tokenPath, false);
        assertOwnerOnlyPermissionsIfSupported(tokenPath.getParent(), true);

        ArtifactUploadTokenProvider secondProvider = new ArtifactUploadTokenProvider(properties);
        secondProvider.initialize();

        assertThat(secondProvider.matches(generatedToken)).isTrue();
    }

    @Test
    void initializeReusesExistingTokenAndTightensFilePermissions() throws IOException {
        Path tokenPath = tempDirectory.resolve("artifact-upload-token");
        Files.writeString(tokenPath, VALID_TOKEN + System.lineSeparator(), StandardCharsets.UTF_8);
        if (supportsPosixPermissions(tokenPath)) {
            Files.setPosixFilePermissions(
                    tokenPath,
                    EnumSet.of(
                            PosixFilePermission.OWNER_READ,
                            PosixFilePermission.OWNER_WRITE,
                            PosixFilePermission.GROUP_READ,
                            PosixFilePermission.OTHERS_READ));
        }
        ArtifactUploadTokenProvider provider =
                new ArtifactUploadTokenProvider(fileProperties(tokenPath));

        provider.initialize();

        assertThat(provider.matches(VALID_TOKEN)).isTrue();
        assertOwnerOnlyPermissionsIfSupported(tokenPath, false);
    }

    @Test
    void initializeRejectsShortTokenReadFromFile() throws IOException {
        Path tokenPath = tempDirectory.resolve("short-token");
        Files.writeString(tokenPath, "too-short\n", StandardCharsets.UTF_8);
        ArtifactUploadTokenProvider provider =
                new ArtifactUploadTokenProvider(fileProperties(tokenPath));

        assertThatThrownBy(provider::initialize)
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("token file")
                .hasMessageContaining("at least 32 characters");
    }

    @Test
    void initializeRejectsSymbolicLinkTokenFile() throws IOException {
        Path target = tempDirectory.resolve("real-token");
        Files.writeString(target, VALID_TOKEN, StandardCharsets.UTF_8);
        Path link = tempDirectory.resolve("linked-token");
        try {
            Files.createSymbolicLink(link, target);
        } catch (UnsupportedOperationException | IOException exception) {
            Assumptions.assumeTrue(false, "Symbolic links are unavailable: " + exception);
        }
        ArtifactUploadTokenProvider provider = new ArtifactUploadTokenProvider(fileProperties(link));

        assertThatThrownBy(provider::initialize)
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("not a safe regular file");
    }

    private SkillSandboxArtifactProperties fileProperties(Path tokenPath) {
        SkillSandboxArtifactProperties properties = new SkillSandboxArtifactProperties();
        properties.setArtifactUploadToken(null);
        properties.setArtifactUploadTokenFile(tokenPath.toString());
        return properties;
    }

    private void assertOwnerOnlyPermissionsIfSupported(Path path, boolean directory)
            throws IOException {
        if (!supportsPosixPermissions(path)) {
            return;
        }
        Set<PosixFilePermission> expected = directory
                ? EnumSet.of(
                        PosixFilePermission.OWNER_READ,
                        PosixFilePermission.OWNER_WRITE,
                        PosixFilePermission.OWNER_EXECUTE)
                : EnumSet.of(PosixFilePermission.OWNER_READ, PosixFilePermission.OWNER_WRITE);
        assertThat(Files.getPosixFilePermissions(path)).isEqualTo(expected);
    }

    private boolean supportsPosixPermissions(Path path) {
        return Files.getFileAttributeView(path, PosixFileAttributeView.class) != null;
    }
}
