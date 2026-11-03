package com.iflytek.astron.console.hub.service.chat.springai;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

import ch.qos.logback.classic.Logger;
import ch.qos.logback.classic.spi.ILoggingEvent;
import ch.qos.logback.core.read.ListAppender;
import com.alibaba.fastjson2.JSONObject;
import com.iflytek.astron.console.toolkit.config.properties.ApiUrl;
import com.iflytek.astron.console.toolkit.security.SandboxRuntimeCredentialTokenProvider;
import com.sun.net.httpserver.HttpServer;
import java.io.IOException;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.concurrent.atomic.AtomicInteger;
import okhttp3.OkHttpClient;
import org.junit.jupiter.api.Test;
import org.slf4j.LoggerFactory;
import org.springframework.test.util.ReflectionTestUtils;

class SkillToolCallbackSecurityTest {

    private static final String SIGV4_QUERY =
            "X-Amz-Algorithm=AWS4-HMAC-SHA256"
                    + "&X-Amz-Credential=test%2F20260824%2Fus-east-1%2Fs3%2Faws4_request"
                    + "&X-Amz-Date=20260824T000000Z&X-Amz-Expires=300"
                    + "&X-Amz-SignedHeaders=host&X-Amz-Signature="
                    + "a".repeat(64);

    @Test
    void runSkillDoesNotLogCommandOrReturnExceptionDetails() throws Exception {
        SkillRuntimeToolService runtime = mock(SkillRuntimeToolService.class);
        String sentinel = "command-secret-and-presigned-url-must-not-leak";
        when(runtime.executeSandbox(any()))
                .thenThrow(new IOException("HTTP 500 https://download.example/?sig=" + sentinel));
        RunSkillToolCallback callback =
                new RunSkillToolCallback(skill("skill-1"), runtime);
        Logger logger = (Logger) LoggerFactory.getLogger(RunSkillToolCallback.class);
        ListAppender<ILoggingEvent> appender = capture(logger);

        String result;
        try {
            result = callback.call(new JSONObject()
                    .fluentPut("command", "echo " + sentinel)
                    .toJSONString());
        } finally {
            stopCapture(logger, appender);
        }

        assertThat(result)
                .contains("run_failed", "skill-1")
                .doesNotContain(sentinel, "download.example", "message");
        assertThat(messages(appender))
                .anySatisfy(message -> assertThat(message)
                        .contains("skillId=skill-1", "commandBytes="))
                .anySatisfy(message -> assertThat(message)
                        .contains("status=failed", "errorType=IOException"))
                .noneSatisfy(message -> assertThat(message).contains(sentinel));
    }

    @Test
    void readSkillDoesNotReturnOrLogDownloadExceptionDetails() throws Exception {
        SkillRuntimeToolService runtime = mock(SkillRuntimeToolService.class);
        String sentinel = "https://download.example/?X-Amz-Signature=must-not-leak";
        when(runtime.downloadText(any())).thenThrow(new IOException(sentinel));
        JSONObject skill = skill("skill-2").fluentPut("downloadUrl", sentinel);
        ReadSkillToolCallback callback = new ReadSkillToolCallback(skill, runtime);
        Logger logger = (Logger) LoggerFactory.getLogger(ReadSkillToolCallback.class);
        ListAppender<ILoggingEvent> appender = capture(logger);

        String result;
        try {
            result = callback.call("{}");
        } finally {
            stopCapture(logger, appender);
        }

        assertThat(result)
                .contains("read_failed", "skill-2")
                .doesNotContain(sentinel, "download.example", "message");
        assertThat(messages(appender))
                .anySatisfy(message -> assertThat(message)
                        .contains("skillId=skill-2", "status=failed"))
                .noneSatisfy(message -> assertThat(message).contains(sentinel));
    }

    @Test
    void sandboxHttpFailureExposesStatusButNeverResponseBody() throws Exception {
        String sentinel = "upstream-secret-and-signed-url-must-not-leak";
        HttpServer server = HttpServer.create(new InetSocketAddress(0), 0);
        server.createContext("/agent/v1/skill/sandbox-exec", exchange -> {
            byte[] response = sentinel.getBytes(StandardCharsets.UTF_8);
            exchange.sendResponseHeaders(502, response.length);
            exchange.getResponseBody().write(response);
            exchange.close();
        });
        server.start();
        try {
            ApiUrl apiUrl = new ApiUrl();
            apiUrl.setAgentUrl("http://127.0.0.1:" + server.getAddress().getPort());
            SandboxRuntimeCredentialTokenProvider tokenProvider =
                    mock(SandboxRuntimeCredentialTokenProvider.class);
            when(tokenProvider.signExecutionRequest(anyLong(), any()))
                    .thenReturn("signature");
            SkillRuntimeToolService runtime = new SkillRuntimeToolService(
                    new OkHttpClient(), apiUrl, tokenProvider);

            assertThatThrownBy(() -> runtime.executeSandbox(new JSONObject()
                    .fluentPut("skill_id", "skill-3")
                    .fluentPut("command", "echo safe")))
                    .isInstanceOf(IOException.class)
                    .hasMessage("sandbox-exec failed: HTTP 502")
                    .hasMessageNotContaining(sentinel);
        } finally {
            server.stop(0);
        }
    }

    @Test
    void skillResourceDownloadRejectsForeignOriginBeforeNetworkAccess() {
        String origin = "http://127.0.0.1:1";
        SkillRuntimeToolService runtime = runtimeForResourceOrigin(origin, 1024);

        List<String> maliciousUrls = List.of(
                signed("http://127.0.0.1:2", "SKILL.md"),
                "http://169.254.169.254/latest/meta-data",
                origin + "/other/skill-files/a.md?" + SIGV4_QUERY,
                origin + "/console-oss/admin/a.md?" + SIGV4_QUERY,
                origin + "/console-oss/skill-files/%2e%2e/admin?" + SIGV4_QUERY,
                origin + "/console-oss/skill-files/a.md",
                signed(origin, "a.md") + "&X-Amz-Signature=" + "b".repeat(64));
        for (String maliciousUrl : maliciousUrls) {
            assertThatThrownBy(() -> runtime.downloadText(maliciousUrl))
                    .isInstanceOf(IOException.class)
                    .hasMessage("Skill resource URL is not allowed");
        }
    }

    @Test
    void skillResourceDownloadDoesNotFollowRedirects() throws Exception {
        AtomicInteger targetRequests = new AtomicInteger();
        HttpServer server = HttpServer.create(new InetSocketAddress(0), 0);
        server.createContext("/console-oss/skill-files/redirect", exchange -> {
            exchange.getResponseHeaders()
                    .add(
                            "Location", "/console-oss/skill-files/SKILL.md?" + SIGV4_QUERY);
            exchange.sendResponseHeaders(302, -1);
            exchange.close();
        });
        server.createContext("/console-oss/skill-files/SKILL.md", exchange -> {
            targetRequests.incrementAndGet();
            byte[] response = "safe".getBytes(StandardCharsets.UTF_8);
            exchange.sendResponseHeaders(200, response.length);
            exchange.getResponseBody().write(response);
            exchange.close();
        });
        server.start();
        try {
            String origin = "http://127.0.0.1:" + server.getAddress().getPort();
            SkillRuntimeToolService runtime = runtimeForResourceOrigin(origin, 1024);

            assertThatThrownBy(() -> runtime.downloadText(signed(origin, "redirect")))
                    .isInstanceOf(IOException.class)
                    .hasMessage("Skill resource download failed: HTTP 302");
            assertThat(targetRequests).hasValue(0);
        } finally {
            server.stop(0);
        }
    }

    @Test
    void skillResourceDownloadRejectsOversizedDeclaredAndChunkedBodies() throws Exception {
        HttpServer server = HttpServer.create(new InetSocketAddress(0), 0);
        server.createContext("/console-oss/skill-files/declared", exchange -> {
            byte[] response = "12345".getBytes(StandardCharsets.UTF_8);
            exchange.sendResponseHeaders(200, response.length);
            exchange.getResponseBody().write(response);
            exchange.close();
        });
        server.createContext("/console-oss/skill-files/chunked", exchange -> {
            byte[] response = "12345".getBytes(StandardCharsets.UTF_8);
            exchange.sendResponseHeaders(200, 0);
            exchange.getResponseBody().write(response);
            exchange.close();
        });
        server.start();
        try {
            String origin = "http://127.0.0.1:" + server.getAddress().getPort();
            SkillRuntimeToolService runtime = runtimeForResourceOrigin(origin, 4);

            assertThatThrownBy(() -> runtime.downloadText(signed(origin, "declared")))
                    .isInstanceOf(IOException.class)
                    .hasMessage("Skill resource exceeds size limit");
            assertThatThrownBy(() -> runtime.downloadText(signed(origin, "chunked")))
                    .isInstanceOf(IOException.class)
                    .hasMessage("Skill resource exceeds size limit");
        } finally {
            server.stop(0);
        }
    }

    @Test
    void skillResourceDownloadAllowsSmallSameOriginText() throws Exception {
        HttpServer server = HttpServer.create(new InetSocketAddress(0), 0);
        server.createContext("/console-oss/skill-files/SKILL.md", exchange -> {
            byte[] response = "safe skill text".getBytes(StandardCharsets.UTF_8);
            exchange.getResponseHeaders().add("Content-Type", "text/plain; charset=utf-8");
            exchange.sendResponseHeaders(200, response.length);
            exchange.getResponseBody().write(response);
            exchange.close();
        });
        server.start();
        try {
            String origin = "http://127.0.0.1:" + server.getAddress().getPort();
            SkillRuntimeToolService runtime = runtimeForResourceOrigin(origin, 1024);

            assertThat(runtime.downloadText(signed(origin, "SKILL.md")))
                    .isEqualTo("safe skill text");
        } finally {
            server.stop(0);
        }
    }

    @Test
    void skillResourceDownloadRejectsUnrepresentableSizeLimit() {
        SkillRuntimeToolService runtime = runtimeForResourceOrigin(
                "http://127.0.0.1:1", Long.MAX_VALUE);

        assertThatThrownBy(() -> runtime.downloadText(signed("http://127.0.0.1:1", "SKILL.md")))
                .isInstanceOf(IOException.class)
                .hasMessage("Skill resource size limit is invalid");
    }

    @Test
    void sandboxSuccessResponseIsStreamedWithHardLimit() throws Exception {
        HttpServer server = HttpServer.create(new InetSocketAddress(0), 0);
        server.createContext("/agent/v1/skill/sandbox-exec", exchange -> {
            byte[] response = "123456".getBytes(StandardCharsets.UTF_8);
            exchange.sendResponseHeaders(200, 0);
            exchange.getResponseBody().write(response);
            exchange.close();
        });
        server.start();
        try {
            ApiUrl apiUrl = new ApiUrl();
            apiUrl.setAgentUrl("http://127.0.0.1:" + server.getAddress().getPort());
            SandboxRuntimeCredentialTokenProvider tokenProvider =
                    mock(SandboxRuntimeCredentialTokenProvider.class);
            when(tokenProvider.signExecutionRequest(anyLong(), any())).thenReturn("signature");
            SkillRuntimeToolService runtime =
                    new SkillRuntimeToolService(new OkHttpClient(), apiUrl, tokenProvider);
            ReflectionTestUtils.setField(runtime, "maxSandboxResponseBytes", 5L);

            assertThatThrownBy(() -> runtime.executeSandbox(new JSONObject()))
                    .isInstanceOf(IOException.class)
                    .hasMessage("Sandbox response exceeds size limit");
        } finally {
            server.stop(0);
        }
    }

    private static SkillRuntimeToolService runtimeForResourceOrigin(String origin, long maxBytes) {
        SkillRuntimeToolService runtime = new SkillRuntimeToolService(
                new OkHttpClient(), new ApiUrl(), mock(SandboxRuntimeCredentialTokenProvider.class));
        ReflectionTestUtils.setField(runtime, "skillResourceOrigin", origin);
        ReflectionTestUtils.setField(runtime, "skillResourceBucket", "console-oss");
        ReflectionTestUtils.setField(runtime, "maxResourceBytes", maxBytes);
        return runtime;
    }

    private static String signed(String origin, String objectName) {
        return origin + "/console-oss/skill-files/" + objectName + "?" + SIGV4_QUERY;
    }

    private static JSONObject skill(String id) {
        return new JSONObject()
                .fluentPut("skillId", id)
                .fluentPut("name", "skill")
                .fluentPut("description", "description");
    }

    private static ListAppender<ILoggingEvent> capture(Logger logger) {
        ListAppender<ILoggingEvent> appender = new ListAppender<>();
        appender.start();
        logger.addAppender(appender);
        return appender;
    }

    private static void stopCapture(
            Logger logger, ListAppender<ILoggingEvent> appender) {
        logger.detachAppender(appender);
        appender.stop();
    }

    private static List<String> messages(ListAppender<ILoggingEvent> appender) {
        return appender.list.stream().map(ILoggingEvent::getFormattedMessage).toList();
    }
}
