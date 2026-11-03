package com.iflytek.astron.console.toolkit.tool.http;

import static org.assertj.core.api.Assertions.assertThat;

import ch.qos.logback.classic.Level;
import ch.qos.logback.classic.Logger;
import ch.qos.logback.classic.spi.ILoggingEvent;
import ch.qos.logback.core.read.ListAppender;
import com.sun.net.httpserver.HttpServer;
import java.io.ByteArrayOutputStream;
import java.io.OutputStream;
import java.io.PrintStream;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.concurrent.CopyOnWriteArrayList;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import org.slf4j.LoggerFactory;

class HeaderAuthHttpToolSecurityTest {

    private HttpServer server;

    @AfterEach
    void tearDown() {
        if (server != null) {
            server.stop(0);
        }
    }

    @Test
    void authenticatedMethodsDoNotLogOrPrintCredentialsOrPayloads() throws Exception {
        String apiKey = "tenant-api-key-sentinel";
        String apiSecret = "tenant-api-secret-sentinel-0123456789abcdef";
        String requestPayload = "{\"credential\":\"request-payload-sentinel\"}";
        String responsePayload = "{\"code\":0,\"data\":[{\"api_key\":\""
                + apiKey
                + "\",\"api_secret\":\""
                + apiSecret
                + "\"}]}";
        List<String> internalKeys = new CopyOnWriteArrayList<>();
        server = HttpServer.create(new InetSocketAddress(0), 0);
        server.createContext("/secure", exchange -> {
            String internalKey =
                    exchange.getRequestHeaders().getFirst("X-Tenant-Internal-Key");
            internalKeys.add(internalKey == null ? "<missing>" : internalKey);
            exchange.getRequestBody().readAllBytes();
            byte[] response = responsePayload.getBytes(StandardCharsets.UTF_8);
            exchange.sendResponseHeaders(200, response.length);
            try (OutputStream output = exchange.getResponseBody()) {
                output.write(response);
            }
        });
        server.start();

        Logger logger = (Logger) LoggerFactory.getLogger(HeaderAuthHttpTool.class);
        Level originalLevel = logger.getLevel();
        ListAppender<ILoggingEvent> appender = new ListAppender<>();
        appender.start();
        logger.setLevel(Level.DEBUG);
        logger.addAppender(appender);
        ByteArrayOutputStream stdout = new ByteArrayOutputStream();
        PrintStream originalOut = System.out;
        List<String> results;
        try {
            System.setOut(new PrintStream(stdout, true, StandardCharsets.UTF_8));
            String url = "http://127.0.0.1:" + server.getAddress().getPort() + "/secure";
            results = List.of(
                    HeaderAuthHttpTool.get(url, apiKey, apiSecret),
                    HeaderAuthHttpTool.post(url, apiKey, apiSecret, requestPayload),
                    HeaderAuthHttpTool.put(url, apiKey, apiSecret, requestPayload),
                    HeaderAuthHttpTool.patch(url, apiKey, apiSecret, requestPayload),
                    HeaderAuthHttpTool.delete(url, apiKey, apiSecret, requestPayload),
                    HeaderAuthHttpTool.tenantGet(url, apiKey, apiSecret));
        } finally {
            System.setOut(originalOut);
            logger.detachAppender(appender);
            logger.setLevel(originalLevel);
            appender.stop();
        }

        assertThat(results).allSatisfy(result -> assertThat(result).contains(apiKey, apiSecret));
        assertThat(internalKeys).containsExactly(
                "<missing>", "<missing>", "<missing>", "<missing>", "<missing>", apiSecret);
        assertThat(appender.list)
                .extracting(ILoggingEvent::getFormattedMessage)
                .hasSize(6)
                .allSatisfy(message -> assertThat(message)
                        .contains("completed")
                        .contains("status=200")
                        .contains("responseChars=")
                        .doesNotContain(apiKey, apiSecret, requestPayload, responsePayload));
        assertThat(stdout.toString(StandardCharsets.UTF_8))
                .doesNotContain(apiKey, apiSecret, requestPayload, responsePayload, "builder:");
    }
}
