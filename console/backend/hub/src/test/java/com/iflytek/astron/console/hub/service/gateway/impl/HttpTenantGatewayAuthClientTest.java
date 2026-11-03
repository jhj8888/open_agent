package com.iflytek.astron.console.hub.service.gateway.impl;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

import ch.qos.logback.classic.Logger;
import ch.qos.logback.classic.spi.ILoggingEvent;
import ch.qos.logback.core.read.ListAppender;
import java.io.IOException;
import okhttp3.Call;
import okhttp3.MediaType;
import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.Response;
import okhttp3.ResponseBody;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.slf4j.LoggerFactory;

@ExtendWith(MockitoExtension.class)
class HttpTenantGatewayAuthClientTest {

    private static final String VERIFY_URL =
            "http://core-tenant:5052/v2/app/key/verify";
    private static final String INTERNAL_KEY =
            "0123456789abcdef0123456789abcdef";
    private static final MediaType JSON_MEDIA_TYPE = MediaType.get("application/json");

    @Mock
    private OkHttpClient httpClient;

    @Mock
    private Call call;

    @Mock
    private Response response;

    private HttpTenantGatewayAuthClient client;

    @BeforeEach
    void setUp() {
        client = new HttpTenantGatewayAuthClient(httpClient, VERIFY_URL, INTERNAL_KEY);
    }

    @Test
    void sendsInternalKeyAndReturnsVerifiedAppId() throws Exception {
        when(httpClient.newCall(any(Request.class))).thenReturn(call);
        when(call.execute()).thenReturn(response);
        when(response.isSuccessful()).thenReturn(true);
        when(response.body()).thenReturn(ResponseBody.create(
                "{\"code\":0,\"data\":{\"appid\":\"app-123\"}}", JSON_MEDIA_TYPE));

        assertThat(client.verify("api-key", "api-secret")).contains("app-123");

        ArgumentCaptor<Request> requestCaptor = ArgumentCaptor.forClass(Request.class);
        verify(httpClient).newCall(requestCaptor.capture());
        Request request = requestCaptor.getValue();
        assertThat(request.method()).isEqualTo("POST");
        assertThat(request.url().toString()).isEqualTo(VERIFY_URL);
        assertThat(request.headers("X-Tenant-Internal-Key"))
                .containsExactly(INTERNAL_KEY);
    }

    @Test
    void missingInternalKeyFailsClosedWithoutCallingTenant() {
        HttpTenantGatewayAuthClient unconfigured =
                new HttpTenantGatewayAuthClient(httpClient, VERIFY_URL, "");

        assertThat(unconfigured.verify("api-key", "api-secret")).isEmpty();

        verifyNoInteractions(httpClient);
    }

    @Test
    void transportFailureDoesNotLogCredentials() throws Exception {
        String apiKey = "api-key-must-not-be-logged";
        String apiSecret = "api-secret-must-not-be-logged";
        when(httpClient.newCall(any(Request.class))).thenReturn(call);
        when(call.execute()).thenThrow(new IOException(
                "upstream failure " + INTERNAL_KEY + " " + apiKey + " " + apiSecret));

        Logger logger =
                (Logger) LoggerFactory.getLogger(HttpTenantGatewayAuthClient.class);
        ListAppender<ILoggingEvent> appender = new ListAppender<>();
        appender.start();
        logger.addAppender(appender);
        try {
            assertThat(client.verify(apiKey, apiSecret)).isEmpty();
        } finally {
            logger.detachAppender(appender);
            appender.stop();
        }

        assertThat(appender.list)
                .extracting(ILoggingEvent::getFormattedMessage)
                .noneSatisfy(message -> assertThat(message)
                        .containsAnyOf(INTERNAL_KEY, apiKey, apiSecret));
    }
}
