package com.iflytek.astron.console.toolkit.security;

import com.iflytek.astron.console.toolkit.config.properties.SandboxRuntimeCredentialProperties;
import jakarta.annotation.PostConstruct;
import java.nio.file.Path;
import java.nio.charset.StandardCharsets;
import java.security.InvalidKeyException;
import java.security.NoSuchAlgorithmException;
import java.util.HexFormat;
import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.apache.commons.lang3.StringUtils;
import org.springframework.stereotype.Component;

/** Matches the credential-broker token without exposing its plaintext value. */
@Component
@Slf4j
@RequiredArgsConstructor
public class SandboxRuntimeCredentialTokenProvider {

    public static final String TOKEN_HEADER =
            "X-Skill-Sandbox-Runtime-Credential-Token";

    private final SandboxRuntimeCredentialProperties properties;
    private byte[] tokenBytes;

    @PostConstruct
    void initialize() {
        String tokenFile = StringUtils.trimToEmpty(properties.getTokenFile());
        tokenBytes = PersistentServiceToken.loadOrCreate(
                properties.getToken(),
                StringUtils.isBlank(tokenFile) ? null : Path.of(tokenFile),
                "Sandbox runtime credential");
        log.info("Sandbox runtime credential initialized");
    }

    public boolean matches(String candidate) {
        return PersistentServiceToken.matches(tokenBytes, candidate);
    }

    boolean matchesCredentialBytes(byte[] candidate) {
        return PersistentServiceToken.matches(tokenBytes, candidate);
    }

    /** Sign the exact core-agent sandbox execution body without exposing the shared credential. */
    public String signExecutionRequest(long epochSeconds, String requestBody) {
        try {
            Mac hmac = Mac.getInstance("HmacSHA256");
            hmac.init(new SecretKeySpec(tokenBytes, "HmacSHA256"));
            byte[] canonical = (epochSeconds + "\n" + StringUtils.defaultString(requestBody))
                    .getBytes(StandardCharsets.UTF_8);
            return HexFormat.of().formatHex(hmac.doFinal(canonical));
        } catch (NoSuchAlgorithmException | InvalidKeyException exception) {
            throw new IllegalStateException(
                    "Unable to sign a sandbox execution request", exception);
        }
    }
}
