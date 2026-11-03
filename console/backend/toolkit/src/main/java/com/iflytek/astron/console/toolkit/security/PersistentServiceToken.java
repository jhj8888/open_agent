package com.iflytek.astron.console.toolkit.security;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.nio.file.StandardOpenOption;
import java.nio.file.attribute.PosixFilePermission;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.util.Base64;
import java.util.EnumSet;
import java.util.Set;
import lombok.extern.slf4j.Slf4j;
import org.apache.commons.lang3.StringUtils;

/** Shared loader for deployment credentials backed by an explicit value or persistent file. */
@Slf4j
final class PersistentServiceToken {

    static final int MIN_TOKEN_LENGTH = 32;
    private static final int GENERATED_TOKEN_BYTES = 48;
    private static final long MAX_TOKEN_FILE_BYTES = 4096;
    private static final SecureRandom SECURE_RANDOM = new SecureRandom();

    private PersistentServiceToken() {}

    static byte[] loadOrCreate(String configuredToken, Path tokenPath, String description) {
        if (StringUtils.containsAny(StringUtils.defaultString(configuredToken), '\r', '\n')) {
            throw new IllegalStateException(
                    description + " configured value must not contain line breaks");
        }
        String normalizedToken = StringUtils.trimToEmpty(configuredToken);
        if (StringUtils.isNotBlank(normalizedToken)) {
            byte[] result = validate(normalizedToken, description + " configured value");
            if (tokenPath != null) {
                persistConfiguredToken(tokenPath, normalizedToken, description);
            }
            return result;
        }
        if (tokenPath == null) {
            throw new IllegalStateException(description + " token is not configured");
        }
        return validate(loadOrCreateTokenFile(tokenPath, description), description + " token file");
    }

    static boolean matches(byte[] expectedTokenBytes, String candidate) {
        byte[] candidateBytes =
                StringUtils.defaultString(candidate).getBytes(StandardCharsets.UTF_8);
        return matches(expectedTokenBytes, candidateBytes);
    }

    static boolean matches(byte[] expectedTokenBytes, byte[] candidateBytes) {
        if (expectedTokenBytes == null || candidateBytes == null) {
            throw new IllegalStateException("Service token is not initialized");
        }
        return MessageDigest.isEqual(expectedTokenBytes, candidateBytes);
    }

    private static String loadOrCreateTokenFile(Path tokenPath, String description) {
        try {
            Path absolutePath = tokenPath.toAbsolutePath().normalize();
            if (Files.exists(absolutePath, LinkOption.NOFOLLOW_LINKS)) {
                return readTokenFile(absolutePath, description);
            }
            Path parent = requireParent(absolutePath, description);
            Files.createDirectories(parent);
            restrictPermissions(parent, true);
            String generated = generateToken();
            try {
                Files.writeString(
                        absolutePath,
                        generated + System.lineSeparator(),
                        StandardCharsets.UTF_8,
                        StandardOpenOption.CREATE_NEW,
                        StandardOpenOption.WRITE);
                restrictPermissions(absolutePath, false);
                return generated;
            } catch (java.nio.file.FileAlreadyExistsException concurrentCreator) {
                return readTokenFile(absolutePath, description);
            }
        } catch (IOException exception) {
            throw new IllegalStateException(
                    "Unable to initialize " + description.toLowerCase() + " token file",
                    exception);
        }
    }

    /** Keep a Compose fallback file aligned so retired credentials cannot reactivate. */
    private static void persistConfiguredToken(Path tokenPath, String token, String description) {
        Path temporaryPath = null;
        try {
            Path absolutePath = tokenPath.toAbsolutePath().normalize();
            if (Files.exists(absolutePath, LinkOption.NOFOLLOW_LINKS)
                    && (Files.isSymbolicLink(absolutePath)
                            || !Files.isRegularFile(absolutePath, LinkOption.NOFOLLOW_LINKS))) {
                throw new IllegalStateException(description + " token file is not a safe regular file");
            }
            Path parent = requireParent(absolutePath, description);
            Files.createDirectories(parent);
            restrictPermissions(parent, true);
            temporaryPath = Files.createTempFile(parent, ".service-token-", ".tmp");
            Files.writeString(
                    temporaryPath,
                    token + System.lineSeparator(),
                    StandardCharsets.UTF_8,
                    StandardOpenOption.TRUNCATE_EXISTING,
                    StandardOpenOption.WRITE);
            restrictPermissions(temporaryPath, false);
            Files.move(
                    temporaryPath,
                    absolutePath,
                    StandardCopyOption.ATOMIC_MOVE,
                    StandardCopyOption.REPLACE_EXISTING);
            temporaryPath = null;
            restrictPermissions(absolutePath, false);
        } catch (IOException exception) {
            throw new IllegalStateException(
                    "Unable to synchronize " + description.toLowerCase() + " token file",
                    exception);
        } finally {
            if (temporaryPath != null) {
                try {
                    Files.deleteIfExists(temporaryPath);
                } catch (IOException exception) {
                    log.warn("Unable to remove a temporary service token file", exception);
                }
            }
        }
    }

    private static String readTokenFile(Path tokenPath, String description) throws IOException {
        if (Files.isSymbolicLink(tokenPath)
                || !Files.isRegularFile(tokenPath, LinkOption.NOFOLLOW_LINKS)
                || Files.size(tokenPath) > MAX_TOKEN_FILE_BYTES) {
            throw new IllegalStateException(description + " token file is not a safe regular file");
        }
        restrictPermissions(tokenPath, false);
        return Files.readString(tokenPath, StandardCharsets.UTF_8).trim();
    }

    private static Path requireParent(Path path, String description) {
        Path parent = path.getParent();
        if (parent == null) {
            throw new IllegalStateException(description + " token file must have a parent directory");
        }
        return parent;
    }

    private static byte[] validate(String token, String source) {
        if (StringUtils.length(token) < MIN_TOKEN_LENGTH
                || StringUtils.containsAny(token, '\r', '\n')) {
            throw new IllegalStateException(
                    source + " must contain at least " + MIN_TOKEN_LENGTH
                            + " characters and no line breaks");
        }
        return token.getBytes(StandardCharsets.UTF_8);
    }

    private static String generateToken() {
        byte[] randomBytes = new byte[GENERATED_TOKEN_BYTES];
        SECURE_RANDOM.nextBytes(randomBytes);
        return Base64.getUrlEncoder().withoutPadding().encodeToString(randomBytes);
    }

    private static void restrictPermissions(Path path, boolean directory) {
        try {
            Set<PosixFilePermission> permissions = directory
                    ? EnumSet.of(
                            PosixFilePermission.OWNER_READ,
                            PosixFilePermission.OWNER_WRITE,
                            PosixFilePermission.OWNER_EXECUTE)
                    : EnumSet.of(
                            PosixFilePermission.OWNER_READ,
                            PosixFilePermission.OWNER_WRITE);
            Files.setPosixFilePermissions(path, permissions);
        } catch (UnsupportedOperationException | IOException exception) {
            log.debug("POSIX permissions are unavailable for a service token path: {}", path);
        }
    }
}
