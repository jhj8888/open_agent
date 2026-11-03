package com.iflytek.astron.console.hub.service.publish.impl;

import com.alibaba.fastjson2.JSONArray;
import com.alibaba.fastjson2.JSONObject;
import com.iflytek.astron.console.commons.security.TenantInternalApiKey;
import com.iflytek.astron.console.hub.dto.user.TenantAuth;
import com.iflytek.astron.console.hub.service.publish.TenantService;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import okhttp3.*;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

import java.util.UUID;
import java.util.concurrent.TimeUnit;

/**
 * @author yun-zhi-ztl
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class TenantServiceImpl implements TenantService {

    @Value("${tenant.create-app}")
    private String createApp;

    @Value("${tenant.get-app-detail}")
    private String getAppDetail;

    @Value("${api.url.tenantSecret:}")
    private String tenantInternalKey;

    private static final OkHttpClient HTTP_CLIENT = new OkHttpClient().newBuilder()
            .connectionPool(new ConnectionPool(100, 5, TimeUnit.MINUTES))
            .connectTimeout(60, TimeUnit.SECONDS)
            .readTimeout(60, TimeUnit.SECONDS)
            .writeTimeout(60, TimeUnit.SECONDS)
            .build();

    @Override
    public String createApp(String uid, String appName, String appDesc) {
        String configuredInternalKey = configuredTenantInternalKey();
        if (configuredInternalKey == null) {
            return null;
        }
        JSONObject requestBody = new JSONObject();
        requestBody.put("request_id", uid + UUID.randomUUID());
        requestBody.put("app_name", appName);
        requestBody.put("app_desc", appDesc);
        requestBody.put("dev_id", 1);
        requestBody.put("cloud_id", "0");

        RequestBody requestBodyForPost = RequestBody.create(MediaType.parse("application/json"), requestBody.toJSONString());
        Request request = new Request.Builder()
                .url(createApp)
                .header(TenantInternalApiKey.HEADER, configuredInternalKey)
                .method("POST", requestBodyForPost)
                .build();

        try (Response response = HTTP_CLIENT.newCall(request).execute()) {
            ResponseBody body = response.body();
            if ((!response.isSuccessful()) || (body == null)) {
                log.error(
                        "Tenant app creation request failed, status={}, hasResponseBody={}",
                        response.code(),
                        body != null);
                return null;
            }
            String responseBody = body.string();
            JSONObject responseJson = JSONObject.parseObject(responseBody);
            if (responseJson.getInteger("code") == 0 && responseJson.containsKey("data") && responseJson.getJSONObject("data").containsKey("app_id")) {
                return responseJson.getJSONObject("data").getString("app_id");
            } else {
                log.error(
                        "Tenant app creation was rejected, code={}",
                        responseJson.getInteger("code"));
            }
        } catch (Exception e) {
            log.error("Tenant app creation request failed: {}", e.getClass().getSimpleName());
        }
        return null;
    }

    @Override
    public TenantAuth getAppDetail(String appId) {
        String configuredInternalKey = configuredTenantInternalKey();
        if (configuredInternalKey == null) {
            return null;
        }
        String requestUrl = String.format("%s?app_ids=%s", getAppDetail, appId);
        Request request = new Request.Builder()
                .url(requestUrl)
                .header(TenantInternalApiKey.HEADER, configuredInternalKey)
                .method("GET", null)
                .build();

        JSONObject reqJson = new JSONObject();
        try (Response response = HTTP_CLIENT.newCall(request).execute()) {
            ResponseBody body = response.body();
            if ((!response.isSuccessful()) || (body == null)) {
                log.error(
                        "Tenant app detail request failed, status={}, hasResponseBody={}",
                        response.code(),
                        body != null);
                return null;
            }
            String responseBody = body.string();
            reqJson = JSONObject.parseObject(responseBody);
            if (reqJson.getInteger("code") == 0 && reqJson.containsKey("data")
                    && reqJson.getJSONArray("data").getJSONObject(0).containsKey("auth_list")) {
                return JSONArray.parseArray(reqJson.getJSONArray("data").getJSONObject(0).getString("auth_list"), TenantAuth.class).get(0);
            } else {
                log.error(
                        "Tenant app detail response was rejected or incomplete, code={}",
                        reqJson.getInteger("code"));
            }
        } catch (Exception e) {
            log.error("Tenant app detail request failed: {}", e.getClass().getSimpleName());
        }
        return null;
    }

    private String configuredTenantInternalKey() {
        try {
            return TenantInternalApiKey.requireConfigured(tenantInternalKey);
        } catch (IllegalStateException exception) {
            log.warn("Tenant internal authentication is not configured; request was not sent");
            return null;
        }
    }

}
