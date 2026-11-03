package com.iflytek.astron.console.hub.platform.model;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.nio.charset.StandardCharsets;
import java.security.SecureRandom;
import java.util.Base64;
import java.util.Map;
import javax.crypto.Cipher;
import javax.crypto.spec.GCMParameterSpec;
import javax.crypto.spec.SecretKeySpec;
import org.springframework.core.env.Environment;
import org.springframework.stereotype.Component;

/** JCE AES-GCM with tenant/environment/version as authenticated associated data. */
@Component
public class ModelCredentialCipher {
    private final Environment environment;
    private final ObjectMapper json;
    private final SecureRandom random = new SecureRandom();

    public ModelCredentialCipher(Environment environment, ObjectMapper json) {
        this.environment = environment;
        this.json = json;
    }

    public record Encrypted(String keyId, byte[] nonce, byte[] ciphertext) {}

    public Encrypted encrypt(String scope, String secret) {
        String keyId = environment.getProperty("MODEL_CREDENTIAL_ACTIVE_KEY", "v1");
        byte[] nonce = new byte[12];
        random.nextBytes(nonce);
        return new Encrypted(keyId, nonce, crypt(Cipher.ENCRYPT_MODE, keyId, nonce, scope, secret.getBytes(StandardCharsets.UTF_8)));
    }

    public String decrypt(String scope, Encrypted encrypted) {
        return new String(crypt(Cipher.DECRYPT_MODE, encrypted.keyId(), encrypted.nonce(), scope, encrypted.ciphertext()), StandardCharsets.UTF_8);
    }

    private byte[] crypt(int mode, String keyId, byte[] nonce, String scope, byte[] input) {
        try {
            Map<String, String> keys = json.readValue(environment.getProperty("MODEL_CREDENTIAL_KEYS", "{}"), new TypeReference<>() {});
            byte[] key = Base64.getDecoder().decode(keys.getOrDefault(keyId, ""));
            if (key.length != 32 || nonce.length != 12) throw new IllegalArgumentException();
            Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
            cipher.init(mode, new SecretKeySpec(key, "AES"), new GCMParameterSpec(128, nonce));
            cipher.updateAAD(scope.getBytes(StandardCharsets.UTF_8));
            return cipher.doFinal(input);
        } catch (Exception exception) {
            throw new IllegalStateException("Model credential configuration is unavailable");
        }
    }
}
