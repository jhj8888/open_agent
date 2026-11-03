package com.iflytek.astron.console.toolkit.service.workflow;

import com.alibaba.fastjson2.JSON;
import com.alibaba.fastjson2.JSONObject;
import com.iflytek.astron.console.toolkit.config.properties.CommonConfig;
import com.sun.net.httpserver.HttpHandler;
import com.sun.net.httpserver.HttpServer;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import org.springframework.test.util.ReflectionTestUtils;

import java.io.IOException;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.atomic.AtomicReference;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/**
 * Exercises {@link WorkflowChatRunClient} against a real HTTP server (JDK's built-in
 * {@link HttpServer} on an ephemeral port) instead of mocking OkHttp internals.
 */
class WorkflowChatRunClientTest {

    private static final String CONTEXT_PATH = "/workflow/v1/chat/completions";
    private static final String INTERNAL_KEY =
            "0123456789abcdef0123456789abcdef";

    private HttpServer server;

    @AfterEach
    void tearDown() {
        if (server != null) {
            server.stop(0);
            server = null;
        }
    }

    private WorkflowChatRunClient newClient(HttpHandler handler) throws IOException {
        server = HttpServer.create(new InetSocketAddress(0), 0);
        server.createContext(CONTEXT_PATH, handler);
        server.start();

        CommonConfig commonConfig = new CommonConfig();
        commonConfig.setAppId("app-id-1");
        commonConfig.setApiKey("key-1");
        commonConfig.setApiSecret("secret-1");

        WorkflowChatRunClient client = new WorkflowChatRunClient(commonConfig);
        ReflectionTestUtils.setField(client, "chatUrl",
                "http://127.0.0.1:" + server.getAddress().getPort() + CONTEXT_PATH);
        ReflectionTestUtils.setField(
                client, "workflowInternalApiKey", INTERNAL_KEY);
        return client;
    }

    @Test
    void chat_success_returnsBodyVerbatimAndSendsExpectedHeadersAndBody() throws Exception {
        String responseJson = "{\"code\":0,\"choices\":[{\"delta\":{\"content\":\"hi\"}}]}";
        AtomicReference<String> capturedAuth = new AtomicReference<>();
        AtomicReference<String> capturedUser = new AtomicReference<>();
        AtomicReference<String> capturedInternalKey = new AtomicReference<>();
        AtomicReference<String> capturedBody = new AtomicReference<>();

        WorkflowChatRunClient client = newClient(exchange -> {
            capturedAuth.set(exchange.getRequestHeaders().getFirst("Authorization"));
            capturedUser.set(exchange.getRequestHeaders().getFirst("X-Consumer-Username"));
            capturedInternalKey.set(
                    exchange.getRequestHeaders().getFirst("X-Workflow-Internal-Key"));
            capturedBody.set(new String(exchange.getRequestBody().readAllBytes(), StandardCharsets.UTF_8));

            byte[] resp = responseJson.getBytes(StandardCharsets.UTF_8);
            exchange.sendResponseHeaders(200, resp.length);
            try (OutputStream os = exchange.getResponseBody()) {
                os.write(resp);
            }
        });

        JSONObject requestBody = new JSONObject();
        requestBody.put("flow_id", "flow123");
        requestBody.put("uid", "user-1");
        requestBody.put("stream", false);

        String result = client.chat(requestBody);

        assertThat(result).isEqualTo(responseJson);
        assertThat(capturedUser.get()).isEqualTo("app-id-1");
        assertThat(capturedAuth.get()).isEqualTo("Bearer key-1:secret-1");
        assertThat(capturedInternalKey.get()).isEqualTo(INTERNAL_KEY);

        JSONObject sentBody = JSON.parseObject(capturedBody.get());
        assertThat(sentBody.getString("flow_id")).isEqualTo("flow123");
        assertThat(sentBody.getBooleanValue("stream")).isFalse();
    }

    @Test
    void chat_nonSuccessStatus_throwsIOExceptionContainingStatusCode() throws Exception {
        WorkflowChatRunClient client = newClient(exchange -> {
            byte[] resp = "server error".getBytes(StandardCharsets.UTF_8);
            exchange.sendResponseHeaders(500, resp.length);
            try (OutputStream os = exchange.getResponseBody()) {
                os.write(resp);
            }
        });

        JSONObject requestBody = new JSONObject().fluentPut("flow_id", "flow123");

        assertThatThrownBy(() -> client.chat(requestBody))
                .isInstanceOf(IOException.class)
                .hasMessageContaining("500");
    }

    @Test
    void chat_emptyResponseBody_returnsEmptyString() throws Exception {
        WorkflowChatRunClient client = newClient(exchange -> {
            exchange.sendResponseHeaders(200, 0);
            exchange.getResponseBody().close();
        });

        String result = client.chat(new JSONObject().fluentPut("flow_id", "flow123"));

        assertThat(result).isEmpty();
    }

    @Test
    void chatRejectsPublishedPlaceholderBeforeIssuingRequest() throws Exception {
        WorkflowChatRunClient client = newClient(exchange -> {
            throw new AssertionError("request must not be issued");
        });
        ReflectionTestUtils.setField(
                client,
                "workflowInternalApiKey",
                "CHANGE_ME_WORKFLOW_INTERNAL_API_KEY");

        assertThatThrownBy(() -> client.chat(new JSONObject()))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageNotContaining("CHANGE_ME_WORKFLOW_INTERNAL_API_KEY");
    }
}
