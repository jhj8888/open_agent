package com.iflytek.astron.console.hub.service.chat.springai;

import com.alibaba.fastjson2.JSONObject;
import com.iflytek.astron.console.toolkit.config.properties.ApiUrl;
import com.iflytek.astron.console.toolkit.security.SandboxRuntimeCredentialTokenProvider;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import okhttp3.MediaType;
import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.RequestBody;
import okhttp3.Response;
import okhttp3.ResponseBody;
import org.apache.commons.lang3.StringUtils;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

import java.io.IOException;
import java.io.InputStream;
import java.net.URI;
import java.net.URISyntaxException;
import java.net.URLDecoder;
import java.nio.charset.Charset;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.time.Instant;
import java.util.Locale;
import java.util.HashMap;
import java.util.Map;
import java.util.Set;

/**
 * HTTP helpers backing the standalone-agent skill tools: downloading SKILL.md / resources for
 * {@code read_skill} (and, in a later phase, calling the sandbox endpoint for {@code run_skill}).
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class SkillRuntimeToolService {

    private static final MediaType JSON_MEDIA_TYPE = MediaType.get("application/json; charset=utf-8");
    private static final long MAX_CONFIGURABLE_RESPONSE_BYTES = 20L * 1024 * 1024;
    private static final String SKILL_OBJECT_PREFIX = "skill-files/";
    private static final Set<String> REQUIRED_SIGV4_PARAMETERS = Set.of(
            "x-amz-algorithm",
            "x-amz-credential",
            "x-amz-date",
            "x-amz-expires",
            "x-amz-signedheaders",
            "x-amz-signature");

    private final OkHttpClient httpClient;
    private final ApiUrl apiUrl;
    private final SandboxRuntimeCredentialTokenProvider runtimeCredentialTokenProvider;

    @Value("${s3.remoteEndpoint}")
    private String skillResourceOrigin;

    @Value("${s3.bucket}")
    private String skillResourceBucket;

    @Value("${skill.runtime.max-resource-bytes:1048576}")
    private long maxResourceBytes = 1048576;

    @Value("${skill.runtime.max-sandbox-response-bytes:2097152}")
    private long maxSandboxResponseBytes = 2097152;

    /**
     * POST a single skill command to core/agent's sandbox-exec endpoint; returns the raw JSON string.
     */
    public String executeSandbox(JSONObject body) throws IOException {
        validateLimit(maxSandboxResponseBytes, "Sandbox response");
        String base = StringUtils.defaultIfBlank(apiUrl.getAgentUrl(), "http://127.0.0.1:8700");
        String url = StringUtils.stripEnd(base, "/") + "/agent/v1/skill/sandbox-exec";
        String requestBody = body.toJSONString();
        long timestamp = Instant.now().getEpochSecond();
        String signature = runtimeCredentialTokenProvider.signExecutionRequest(
                timestamp, requestBody);
        Request request = new Request.Builder()
                .url(url)
                .post(RequestBody.create(requestBody, JSON_MEDIA_TYPE))
                .addHeader("Content-Type", "application/json")
                .addHeader(
                        "X-Skill-Sandbox-Execution-Timestamp",
                        String.valueOf(timestamp))
                .addHeader("X-Skill-Sandbox-Execution-Signature", signature)
                .build();
        OkHttpClient client = httpClient.newBuilder()
                .callTimeout(Duration.ofSeconds(120))
                .followRedirects(false)
                .followSslRedirects(false)
                .build();
        try (Response response = client.newCall(request).execute()) {
            if (!response.isSuccessful()) {
                throw new IOException("sandbox-exec failed: HTTP " + response.code());
            }
            ResponseBody respBody = response.body();
            return respBody == null
                    ? ""
                    : decodeText(
                            readBounded(respBody, maxSandboxResponseBytes, "Sandbox response"),
                            respBody);
        }
    }

    /** Download a text resource (SKILL.md or a referenced file) from a presigned URL. */
    public String downloadText(String url) throws IOException {
        validateLimit(maxResourceBytes, "Skill resource");
        validateResourceUrl(url);
        Request request;
        try {
            request = new Request.Builder().url(url).get().build();
        } catch (IllegalArgumentException exception) {
            throw new IOException("Skill resource URL is not allowed");
        }
        OkHttpClient client = httpClient.newBuilder()
                .callTimeout(Duration.ofSeconds(30))
                .followRedirects(false)
                .followSslRedirects(false)
                .build();
        try (Response response = client.newCall(request).execute()) {
            if (!response.isSuccessful()) {
                throw new IOException("Skill resource download failed: HTTP " + response.code());
            }
            ResponseBody body = response.body();
            if (body == null) {
                throw new IOException("Skill resource download returned empty body");
            }
            return decodeText(readBounded(body, maxResourceBytes, "Skill resource"), body);
        }
    }

    private byte[] readBounded(ResponseBody body, long limit, String description)
            throws IOException {
        validateLimit(limit, description);
        long contentLength = body.contentLength();
        if (contentLength > limit) {
            throw new IOException(description + " exceeds size limit");
        }
        int readLimit = Math.toIntExact(limit + 1);
        try (InputStream input = body.byteStream()) {
            byte[] bytes = input.readNBytes(readLimit);
            if (bytes.length > limit) {
                throw new IOException(description + " exceeds size limit");
            }
            return bytes;
        }
    }

    private void validateLimit(long limit, String description) throws IOException {
        if (limit < 1 || limit > MAX_CONFIGURABLE_RESPONSE_BYTES) {
            throw new IOException(description + " size limit is invalid");
        }
    }

    private String decodeText(byte[] bytes, ResponseBody body) {
        MediaType contentType = body.contentType();
        if (contentType == null) {
            return new String(bytes, StandardCharsets.UTF_8);
        }
        Charset configuredCharset = contentType.charset();
        Charset charset = configuredCharset == null ? StandardCharsets.UTF_8 : configuredCharset;
        return new String(bytes, charset);
    }

    private void validateResourceUrl(String url) throws IOException {
        final URI candidate;
        final URI configuredOrigin;
        try {
            candidate = new URI(StringUtils.trimToEmpty(url));
            configuredOrigin = new URI(StringUtils.trimToEmpty(skillResourceOrigin));
        } catch (URISyntaxException exception) {
            throw new IOException("Skill resource URL is not allowed");
        }
        validateConfiguredOrigin(configuredOrigin);
        validateCandidateOrigin(candidate, configuredOrigin);
        validateResourcePath(candidate, configuredOrigin);
        validateSigV4Query(candidate.getRawQuery());
    }

    private void validateConfiguredOrigin(URI configuredOrigin) throws IOException {
        String configuredScheme = normalizeScheme(configuredOrigin.getScheme());
        if (!("http".equals(configuredScheme) || "https".equals(configuredScheme))
                || StringUtils.isBlank(configuredOrigin.getHost())
                || configuredOrigin.getRawUserInfo() != null
                || configuredOrigin.getRawQuery() != null
                || configuredOrigin.getRawFragment() != null) {
            throw new IOException("Skill resource URL is not allowed");
        }
    }

    private void validateCandidateOrigin(URI candidate, URI configuredOrigin) throws IOException {
        String candidateScheme = normalizeScheme(candidate.getScheme());
        String configuredScheme = normalizeScheme(configuredOrigin.getScheme());
        if (!("http".equals(candidateScheme) || "https".equals(candidateScheme))
                || !candidateScheme.equals(configuredScheme)
                || !StringUtils.equalsIgnoreCase(candidate.getHost(), configuredOrigin.getHost())
                || effectivePort(candidate) != effectivePort(configuredOrigin)
                || candidate.getRawUserInfo() != null
                || candidate.getRawFragment() != null) {
            throw new IOException("Skill resource URL is not allowed");
        }
    }

    private void validateResourcePath(URI candidate, URI configuredOrigin) throws IOException {
        if (StringUtils.isBlank(candidate.getRawPath())
                || candidate.getRawPath().contains("\\")
                || hasUnsafePathSegment(candidate.getPath())) {
            throw new IOException("Skill resource URL is not allowed");
        }
        String configuredPath = StringUtils.removeEnd(
                StringUtils.defaultIfBlank(configuredOrigin.getPath(), ""), "/");
        String candidatePath = StringUtils.defaultString(candidate.getPath());
        String bucket = StringUtils.trimToEmpty(skillResourceBucket);
        String requiredPrefix = configuredPath + "/" + bucket + "/" + SKILL_OBJECT_PREFIX;
        if (StringUtils.isBlank(bucket)
                || !candidatePath.startsWith(requiredPrefix)
                || candidatePath.length() <= requiredPrefix.length()) {
            throw new IOException("Skill resource URL is not allowed");
        }
    }

    private String normalizeScheme(String scheme) {
        return StringUtils.defaultString(scheme).toLowerCase(Locale.ROOT);
    }

    private int effectivePort(URI uri) {
        if (uri.getPort() >= 0) {
            return uri.getPort();
        }
        return "https".equalsIgnoreCase(uri.getScheme()) ? 443 : 80;
    }

    private boolean hasUnsafePathSegment(String path) {
        if (path == null) {
            return true;
        }
        for (String segment : path.split("/", -1)) {
            if (".".equals(segment) || "..".equals(segment)) {
                return true;
            }
        }
        return false;
    }

    private void validateSigV4Query(String rawQuery) throws IOException {
        if (StringUtils.isBlank(rawQuery)) {
            throw new IOException("Skill resource URL is not allowed");
        }
        Map<String, String> parameters = new HashMap<>();
        try {
            for (String pair : rawQuery.split("&", -1)) {
                int separator = pair.indexOf('=');
                if (separator <= 0) {
                    throw new IllegalArgumentException("invalid query parameter");
                }
                String key = URLDecoder.decode(
                        pair.substring(0, separator), StandardCharsets.UTF_8)
                        .toLowerCase(Locale.ROOT);
                String value = URLDecoder.decode(
                        pair.substring(separator + 1), StandardCharsets.UTF_8);
                if (StringUtils.isBlank(key)
                        || StringUtils.isBlank(value)
                        || parameters.putIfAbsent(key, value) != null) {
                    throw new IllegalArgumentException("duplicate or blank query parameter");
                }
            }
        } catch (IllegalArgumentException exception) {
            throw new IOException("Skill resource URL is not allowed");
        }
        if (!parameters.keySet().containsAll(REQUIRED_SIGV4_PARAMETERS)
                || !"AWS4-HMAC-SHA256".equals(parameters.get("x-amz-algorithm"))
                || !parameters.get("x-amz-date").matches("\\d{8}T\\d{6}Z")
                || !parameters.get("x-amz-signature").matches("(?i)[0-9a-f]{64}")) {
            throw new IOException("Skill resource URL is not allowed");
        }
        try {
            long expiry = Long.parseLong(parameters.get("x-amz-expires"));
            if (expiry < 1 || expiry > 604800) {
                throw new NumberFormatException("expiry outside SigV4 range");
            }
        } catch (NumberFormatException exception) {
            throw new IOException("Skill resource URL is not allowed");
        }
    }
}
