package com.iflytek.astron.console.toolkit.service.external;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

import com.alibaba.fastjson2.JSON;
import com.alibaba.fastjson2.JSONObject;
import java.io.IOException;
import java.util.Optional;
import okhttp3.Call;
import okhttp3.MediaType;
import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.Response;
import okhttp3.ResponseBody;
import okio.Buffer;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

@ExtendWith(MockitoExtension.class)
class ExternalApiServiceTest {

    private static final String VERIFY_URL = "http://core-tenant:5052/v2/app/key/verify";
    private static final String INTERNAL_KEY = "0123456789abcdef0123456789abcdef";
    private static final MediaType JSON_MEDIA_TYPE = MediaType.get("application/json");

    @Mock
    private OkHttpClient httpClient;
    @Mock
    private Call call;
    @Mock
    private Response response;

    private ExternalApiService service;

    @BeforeEach
    void setUp() {
        service = new ExternalApiService(httpClient, VERIFY_URL, INTERNAL_KEY);
    }

    @Test
    void verifiesCompleteCredentialPairAndReturnsAppId() throws Exception {
        when(httpClient.newCall(any(Request.class))).thenReturn(call);
        when(call.execute()).thenReturn(response);
        when(response.isSuccessful()).thenReturn(true);
        when(response.body()).thenReturn(jsonBody(
                "{\"sid\":\"sid-1\",\"code\":0,\"data\":{\"appid\":\"app-123\"}}"));

        Optional<String> appId = service.verifyAppCredentials("api-key", "api-secret");

        assertThat(appId).contains("app-123");
        ArgumentCaptor<Request> requestCaptor = ArgumentCaptor.forClass(Request.class);
        verify(httpClient).newCall(requestCaptor.capture());
        Request request = requestCaptor.getValue();
        assertThat(request.method()).isEqualTo("POST");
        assertThat(request.url().toString()).isEqualTo(VERIFY_URL);
        assertThat(request.headers("X-Tenant-Internal-Key"))
                .containsExactly(INTERNAL_KEY);

        Buffer buffer = new Buffer();
        request.body().writeTo(buffer);
        JSONObject payload = JSON.parseObject(buffer.readUtf8());
        assertThat(payload).containsEntry("api_key", "api-key").containsEntry("api_secret", "api-secret");
    }

    @Test
    void nonSuccessfulHttpResponseFailsClosedWithoutReadingBody() throws Exception {
        when(httpClient.newCall(any(Request.class))).thenReturn(call);
        when(call.execute()).thenReturn(response);
        when(response.isSuccessful()).thenReturn(false);
        when(response.code()).thenReturn(503);

        assertThat(service.verifyAppCredentials("api-key", "api-secret")).isEmpty();
        verify(response, never()).body();
    }

    @Test
    void tenantRejectionAndMalformedResponseFailClosed() throws Exception {
        when(httpClient.newCall(any(Request.class))).thenReturn(call);
        when(call.execute()).thenReturn(response);
        when(response.isSuccessful()).thenReturn(true);
        when(response.body())
                .thenReturn(jsonBody("{\"code\":10001,\"message\":\"invalid credentials\"}"))
                .thenReturn(jsonBody("not-json"))
                .thenReturn(jsonBody("{\"code\":0,\"data\":{}}"));

        assertThat(service.verifyAppCredentials("api-key", "wrong-secret")).isEmpty();
        assertThat(service.verifyAppCredentials("api-key", "api-secret")).isEmpty();
        assertThat(service.verifyAppCredentials("api-key", "api-secret")).isEmpty();
    }

    @Test
    void transportFailureFailsClosed() throws Exception {
        when(httpClient.newCall(any(Request.class))).thenReturn(call);
        when(call.execute()).thenThrow(new IOException("credential-bearing upstream failure"));

        assertThat(service.verifyAppCredentials("api-key", "api-secret")).isEmpty();
    }

    @Test
    void incompleteConfigurationOrCredentialsDoNotCallTenant() {
        assertThat(service.verifyAppCredentials("", "api-secret")).isEmpty();
        assertThat(service.verifyAppCredentials("api-key", " ")).isEmpty();
        assertThat(new ExternalApiService(httpClient, " ", INTERNAL_KEY)
                .verifyAppCredentials("api-key", "api-secret"))
                .isEmpty();

        verifyNoInteractions(httpClient);
    }

    @Test
    void missingOrInvalidInternalKeyFailsClosedWithoutCallingTenant() {
        assertThat(new ExternalApiService(httpClient, VERIFY_URL, null)
                .verifyAppCredentials("api-key", "api-secret"))
                .isEmpty();
        assertThat(new ExternalApiService(httpClient, VERIFY_URL, "short")
                .verifyAppCredentials("api-key", "api-secret"))
                .isEmpty();
        assertThat(new ExternalApiService(
                httpClient,
                VERIFY_URL,
                "0123456789abcdef0123456789abcde!")
                .verifyAppCredentials("api-key", "api-secret"))
                .isEmpty();

        verifyNoInteractions(httpClient);
    }

    private static ResponseBody jsonBody(String body) {
        return ResponseBody.create(body, JSON_MEDIA_TYPE);
    }
}
