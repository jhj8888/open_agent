package com.iflytek.astron.console.hub.service.publish.impl;

import static org.assertj.core.api.Assertions.assertThat;

import ch.qos.logback.classic.Logger;
import ch.qos.logback.classic.spi.ILoggingEvent;
import ch.qos.logback.core.read.ListAppender;
import com.sun.net.httpserver.HttpServer;
import java.io.IOException;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import org.slf4j.LoggerFactory;
import org.springframework.test.util.ReflectionTestUtils;

class TenantServiceImplSecurityTest {

    private static final String INTERNAL_KEY =
            "0123456789abcdef0123456789abcdef";

    private HttpServer server;

    @AfterEach
    void tearDown() {
        if (server != null) {
            server.stop(0);
        }
    }

    @Test
    void createAppDoesNotLogRequestOrCredentialBearingResponse() throws IOException {
        String uid = "tenant-uid-must-not-be-logged";
        String appName = "tenant-app-name-must-not-be-logged";
        String appDescription = "tenant-app-description-must-not-be-logged";
        String apiKey = "created-api-key-must-not-be-logged";
        String apiSecret = "created-api-secret-must-not-be-logged";
        AtomicReference<String> capturedInternalKey = new AtomicReference<>();
        server = HttpServer.create(new InetSocketAddress(0), 0);
        server.createContext("/v2/app", exchange -> {
            capturedInternalKey.set(
                    exchange.getRequestHeaders().getFirst("X-Tenant-Internal-Key"));
            exchange.getRequestBody().readAllBytes();
            byte[] response = ("{\"code\":403,\"data\":{\"api_key\":\""
                    + apiKey
                    + "\",\"api_secret\":\""
                    + apiSecret
                    + "\"}}")
                    .getBytes(StandardCharsets.UTF_8);
            exchange.sendResponseHeaders(200, response.length);
            try (OutputStream output = exchange.getResponseBody()) {
                output.write(response);
            }
        });
        server.start();

        TenantServiceImpl service = new TenantServiceImpl();
        ReflectionTestUtils.setField(
                service,
                "createApp",
                "http://127.0.0.1:" + server.getAddress().getPort() + "/v2/app");
        ReflectionTestUtils.setField(service, "tenantInternalKey", INTERNAL_KEY);

        Logger logger = (Logger) LoggerFactory.getLogger(TenantServiceImpl.class);
        ListAppender<ILoggingEvent> appender = new ListAppender<>();
        appender.start();
        logger.addAppender(appender);
        try {
            assertThat(service.createApp(uid, appName, appDescription)).isNull();
        } finally {
            logger.detachAppender(appender);
            appender.stop();
        }

        assertThat(appender.list)
                .extracting(ILoggingEvent::getFormattedMessage)
                .anySatisfy(message -> assertThat(message)
                        .contains("Tenant app creation was rejected")
                        .contains("code=403"))
                .noneSatisfy(message -> assertThat(message)
                        .containsAnyOf(
                                uid,
                                appName,
                                appDescription,
                                apiKey,
                                apiSecret,
                                INTERNAL_KEY));
        assertThat(capturedInternalKey).hasValue(INTERNAL_KEY);
    }

    @Test
    void getAppDetailDoesNotLogAppIdOrCredentialBearingResponse() throws IOException {
        String appId = "tenant-app-id-must-not-be-logged";
        String apiKey = "detail-api-key-must-not-be-logged";
        String apiSecret = "detail-api-secret-must-not-be-logged";
        AtomicReference<String> capturedInternalKey = new AtomicReference<>();
        server = HttpServer.create(new InetSocketAddress(0), 0);
        server.createContext("/v2/app/details", exchange -> {
            capturedInternalKey.set(
                    exchange.getRequestHeaders().getFirst("X-Tenant-Internal-Key"));
            byte[] response = ("{\"code\":403,\"data\":[{\"auth_list\":[{\"api_key\":\""
                    + apiKey
                    + "\",\"api_secret\":\""
                    + apiSecret
                    + "\"}]}]}")
                    .getBytes(StandardCharsets.UTF_8);
            exchange.sendResponseHeaders(200, response.length);
            try (OutputStream output = exchange.getResponseBody()) {
                output.write(response);
            }
        });
        server.start();

        TenantServiceImpl service = new TenantServiceImpl();
        ReflectionTestUtils.setField(
                service,
                "getAppDetail",
                "http://127.0.0.1:"
                        + server.getAddress().getPort()
                        + "/v2/app/details");
        ReflectionTestUtils.setField(service, "tenantInternalKey", INTERNAL_KEY);

        Logger logger = (Logger) LoggerFactory.getLogger(TenantServiceImpl.class);
        ListAppender<ILoggingEvent> appender = new ListAppender<>();
        appender.start();
        logger.addAppender(appender);
        try {
            assertThat(service.getAppDetail(appId)).isNull();
        } finally {
            logger.detachAppender(appender);
            appender.stop();
        }

        assertThat(appender.list)
                .extracting(ILoggingEvent::getFormattedMessage)
                .anySatisfy(message -> assertThat(message)
                        .contains("Tenant app detail response was rejected or incomplete")
                        .contains("code=403"))
                .noneSatisfy(message -> assertThat(message)
                        .containsAnyOf(appId, apiKey, apiSecret, INTERNAL_KEY));
        assertThat(capturedInternalKey).hasValue(INTERNAL_KEY);
    }

    @Test
    void missingInternalKeyFailsClosedBeforeAnyTenantRequest() throws IOException {
        AtomicInteger requestCount = new AtomicInteger();
        server = HttpServer.create(new InetSocketAddress(0), 0);
        server.createContext("/", exchange -> {
            requestCount.incrementAndGet();
            exchange.sendResponseHeaders(204, -1);
            exchange.close();
        });
        server.start();

        String baseUrl = "http://127.0.0.1:" + server.getAddress().getPort();
        TenantServiceImpl service = new TenantServiceImpl();
        ReflectionTestUtils.setField(service, "createApp", baseUrl + "/v2/app");
        ReflectionTestUtils.setField(
                service, "getAppDetail", baseUrl + "/v2/app/details");
        ReflectionTestUtils.setField(service, "tenantInternalKey", "short");

        assertThat(service.createApp("uid", "name", "description")).isNull();
        assertThat(service.getAppDetail("app-id")).isNull();
        assertThat(requestCount).hasValue(0);
    }
}
