package com.iflytek.astron.console.commons.util;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

import ch.qos.logback.classic.Logger;
import ch.qos.logback.classic.spi.ILoggingEvent;
import ch.qos.logback.core.read.ListAppender;
import com.iflytek.astron.console.commons.dto.bot.BotCreateForm;
import com.sun.net.httpserver.HttpServer;
import jakarta.servlet.http.HttpServletRequest;
import java.io.IOException;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.concurrent.CopyOnWriteArrayList;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import org.slf4j.LoggerFactory;
import org.springframework.test.util.ReflectionTestUtils;

class MaasUtilWorkflowInternalAuthTest {

    private static final String INTERNAL_KEY =
            "internal-key-0123456789abcdef0123456789abcdef";
    private static final String CONSUMER_KEY = "consumer-key-must-not-be-logged";
    private static final String CONSUMER_SECRET = "consumer-secret-must-not-be-logged";

    private HttpServer server;

    @AfterEach
    void tearDown() {
        if (server != null) {
            server.stop(0);
        }
    }

    @Test
    void createApiSendsInternalKeyWithoutLoggingCredentials() throws IOException {
        List<String> internalKeys = new CopyOnWriteArrayList<>();
        List<String> authorizations = new CopyOnWriteArrayList<>();
        List<String> consumers = new CopyOnWriteArrayList<>();
        server = HttpServer.create(new InetSocketAddress(0), 0);
        server.createContext("/", exchange -> {
            internalKeys.add(exchange.getRequestHeaders()
                    .getFirst("X-Workflow-Internal-Key"));
            authorizations.add(exchange.getRequestHeaders().getFirst("Authorization"));
            consumers.add(exchange.getRequestHeaders().getFirst("X-Consumer-Username"));
            exchange.getRequestBody().readAllBytes();
            byte[] response = "{\"code\":0}".getBytes(StandardCharsets.UTF_8);
            exchange.sendResponseHeaders(200, response.length);
            try (OutputStream output = exchange.getResponseBody()) {
                output.write(response);
            }
        });
        server.start();

        String baseUrl = "http://127.0.0.1:" + server.getAddress().getPort();
        MaasUtil maasUtil = new MaasUtil();
        ReflectionTestUtils.setField(maasUtil, "publishApi", baseUrl + "/publish");
        ReflectionTestUtils.setField(maasUtil, "authApi", baseUrl + "/auth");
        ReflectionTestUtils.setField(maasUtil, "consumerId", "consumer-app");
        ReflectionTestUtils.setField(maasUtil, "consumerKey", CONSUMER_KEY);
        ReflectionTestUtils.setField(maasUtil, "consumerSecret", CONSUMER_SECRET);
        ReflectionTestUtils.setField(
                maasUtil, "workflowInternalApiKey", INTERNAL_KEY);

        Logger logger = (Logger) LoggerFactory.getLogger(MaasUtil.class);
        ListAppender<ILoggingEvent> appender = new ListAppender<>();
        appender.start();
        logger.addAppender(appender);
        try {
            maasUtil.createApi("flow-1", "target-app");
        } finally {
            logger.detachAppender(appender);
            appender.stop();
        }

        assertThat(internalKeys).containsExactly(INTERNAL_KEY, INTERNAL_KEY);
        assertThat(authorizations)
                .containsExactly(
                        "Bearer " + CONSUMER_KEY + ":" + CONSUMER_SECRET,
                        "Bearer " + CONSUMER_KEY + ":" + CONSUMER_SECRET);
        assertThat(consumers).containsExactly("consumer-app", "consumer-app");
        assertThat(appender.list)
                .extracting(ILoggingEvent::getFormattedMessage)
                .anySatisfy(message -> assertThat(message)
                        .contains("MaaS workflow API request")
                        .contains("bodyBytes="))
                .noneSatisfy(message -> assertThat(message)
                        .containsAnyOf(INTERNAL_KEY, CONSUMER_KEY, CONSUMER_SECRET));
    }

    @Test
    void synchronizeWorkflowLogsOnlySafeRequestAndResponseSummaries() throws IOException {
        String authorization = "Bearer synchronization-credential-must-not-be-logged";
        String requestSecret = "synchronization-request-secret";
        String responseSecret = "synchronization-response-secret";
        server = HttpServer.create(new InetSocketAddress(0), 0);
        server.createContext("/synchronize", exchange -> {
            exchange.getRequestBody().readAllBytes();
            byte[] response = ("{\"code\":401,\"api_secret\":\"" + responseSecret + "\"}")
                    .getBytes(StandardCharsets.UTF_8);
            exchange.sendResponseHeaders(200, response.length);
            try (OutputStream output = exchange.getResponseBody()) {
                output.write(response);
            }
        });
        server.start();

        MaasUtil maasUtil = new MaasUtil();
        ReflectionTestUtils.setField(
                maasUtil,
                "synchronizeUrl",
                "http://127.0.0.1:" + server.getAddress().getPort() + "/synchronize");
        ReflectionTestUtils.setField(maasUtil, "maasAppId", "maas-app");
        BotCreateForm bot = new BotCreateForm();
        bot.setBotId(1);
        bot.setName(requestSecret);
        bot.setBotDesc(requestSecret);
        bot.setPrologue(requestSecret);
        HttpServletRequest request = mock(HttpServletRequest.class);
        when(request.getHeader("Authorization")).thenReturn(authorization);

        Logger logger = (Logger) LoggerFactory.getLogger(MaasUtil.class);
        ListAppender<ILoggingEvent> appender = new ListAppender<>();
        appender.start();
        logger.addAppender(appender);
        try {
            assertThat(maasUtil.synchronizeWorkFlow(null, bot, request, 7L, 3, null)).isEmpty();
        } finally {
            logger.detachAppender(appender);
            appender.stop();
        }

        assertThat(appender.list)
                .extracting(ILoggingEvent::getFormattedMessage)
                .anySatisfy(message -> assertThat(message)
                        .contains("MaaS workflow synchronization request prepared")
                        .contains("method=POST")
                        .contains("bodyBytes="))
                .anySatisfy(message -> assertThat(message)
                        .contains("MaaS workflow synchronization was rejected")
                        .contains("code=401"))
                .noneSatisfy(message -> assertThat(message)
                        .containsAnyOf(authorization, requestSecret, responseSecret));
    }
}
