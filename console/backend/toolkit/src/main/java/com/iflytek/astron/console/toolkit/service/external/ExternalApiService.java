package com.iflytek.astron.console.toolkit.service.external;

import com.alibaba.fastjson2.JSON;
import com.alibaba.fastjson2.JSONObject;
import com.iflytek.astron.console.commons.security.TenantInternalApiKey;
import com.iflytek.astron.console.toolkit.entity.dto.external.AppInfoResponse;
import lombok.extern.slf4j.Slf4j;
import okhttp3.MediaType;
import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.RequestBody;
import okhttp3.Response;
import okhttp3.ResponseBody;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.util.StringUtils;

import java.io.IOException;
import java.util.Optional;

/**
 * Service for calling external third-party APIs
 */
@Service
@Slf4j
public class ExternalApiService {
    private static final MediaType JSON_MEDIA_TYPE = MediaType.parse("application/json; charset=utf-8");

    private final OkHttpClient httpClient;
    private final String appAuthVerifyUrl;
    private final String tenantInternalKey;

    @Autowired
    public ExternalApiService(
            @Value("${api.url.appAuthVerifyUrl}") String appAuthVerifyUrl,
            @Value("${api.url.apiSecret:}") String tenantInternalKey) {
        this(new OkHttpClient(), appAuthVerifyUrl, tenantInternalKey);
    }

    ExternalApiService(
            OkHttpClient httpClient, String appAuthVerifyUrl, String tenantInternalKey) {
        this.httpClient = httpClient;
        this.appAuthVerifyUrl = appAuthVerifyUrl;
        this.tenantInternalKey = tenantInternalKey;
    }

    /**
     * Verify the complete application credential pair with Tenant.
     *
     * @param apiKey API key
     * @param apiSecret API secret
     * @return verified application ID, or empty when verification cannot be completed
     */
    public Optional<String> verifyAppCredentials(String apiKey, String apiSecret) {
        if (!StringUtils.hasText(appAuthVerifyUrl)
                || !StringUtils.hasText(apiKey)
                || !StringUtils.hasText(apiSecret)) {
            log.warn("Tenant application credential verification is not configured or incomplete");
            return Optional.empty();
        }
        String configuredInternalKey;
        try {
            configuredInternalKey =
                    TenantInternalApiKey.requireConfigured(tenantInternalKey);
        } catch (IllegalStateException exception) {
            log.warn("Tenant internal authentication is not configured; verification was not sent");
            return Optional.empty();
        }

        JSONObject requestJson = new JSONObject();
        requestJson.put("api_key", apiKey);
        requestJson.put("api_secret", apiSecret);
        Request request = new Request.Builder()
                .url(appAuthVerifyUrl)
                .header(TenantInternalApiKey.HEADER, configuredInternalKey)
                .post(RequestBody.create(requestJson.toJSONString(), JSON_MEDIA_TYPE))
                .build();

        try (Response response = httpClient.newCall(request).execute()) {
            if (!response.isSuccessful()) {
                log.warn(
                        "Tenant application credential verification failed, status={}",
                        response.code());
                return Optional.empty();
            }
            ResponseBody body = response.body();
            if (body == null) {
                log.warn("Tenant application credential verification returned an empty response");
                return Optional.empty();
            }
            AppInfoResponse parsed = JSON.parseObject(body.string(), AppInfoResponse.class);
            if (parsed == null || parsed.getCode() == null || parsed.getCode() != 0) {
                return Optional.empty();
            }
            AppInfoResponse.AppInfoData data = parsed.getData();
            return data != null && StringUtils.hasText(data.getAppid())
                    ? Optional.of(data.getAppid())
                    : Optional.empty();
        } catch (IOException | RuntimeException exception) {
            // Never include the request body, API key, API secret, or remote response in logs.
            log.warn(
                    "Tenant application credential verification request failed: {}",
                    exception.getClass().getSimpleName());
            return Optional.empty();
        }
    }
}
