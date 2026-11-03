package com.iflytek.astron.console.hub.service.chat.springai;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.mockStatic;
import static org.mockito.Mockito.when;

import ch.qos.logback.classic.Logger;
import ch.qos.logback.classic.spi.ILoggingEvent;
import ch.qos.logback.core.read.ListAppender;
import com.iflytek.astron.console.commons.service.ChatRecordModelService;
import com.iflytek.astron.console.commons.service.data.ChatDataService;
import com.iflytek.astron.console.commons.util.SseEmitterUtil;
import com.iflytek.astron.console.hub.service.agentmemory.runtime.AgentMemoryRuntimeService;
import java.nio.charset.StandardCharsets;
import java.util.List;
import org.junit.jupiter.api.Test;
import org.mockito.MockedStatic;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpHeaders;
import org.springframework.test.util.ReflectionTestUtils;
import org.springframework.web.reactive.function.client.WebClientResponseException;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

class SpringAiAgentChatServiceSecurityTest {

    @Test
    void setupFailureReturnsFixedMessageAndLogsOnlyErrorType() {
        String sentinel = "https://model.example/v1?api_key=must-not-leak";
        ChatModelFactory chatModelFactory = mock(ChatModelFactory.class);
        when(chatModelFactory.forSparkModel(any())).thenThrow(new IllegalStateException(sentinel));
        SpringAiAgentChatService service = service(chatModelFactory);
        SseEmitter emitter = mock(SseEmitter.class);
        String streamId = "stream-setup-1";
        AgentChatTask task = AgentChatTask.builder()
                .sparkModelName("spark-x1")
                .mcpServerUrls(sentinel)
                .tools(sentinel)
                .workflows(sentinel)
                .build();
        Logger logger = (Logger) LoggerFactory.getLogger(SpringAiAgentChatService.class);
        ListAppender<ILoggingEvent> appender = capture(logger);

        try (MockedStatic<SseEmitterUtil> sse = mockStatic(SseEmitterUtil.class)) {
            service.chat(task, emitter, streamId);

            sse.verify(() -> SseEmitterUtil.completeWithError(
                    emitter, "Failed to process chat request (streamId: " + streamId + ")"));
        } finally {
            stopCapture(logger, appender);
        }

        assertThat(messages(appender))
                .anySatisfy(message -> assertThat(message)
                        .contains("streamId=" + streamId, "errorType=IllegalStateException"))
                .noneSatisfy(message -> assertThat(message).contains(sentinel, "api_key"));
    }

    @Test
    void webClientFailureDoesNotLogOrReturnResponseBody() {
        String sentinel = "protocol-secret-and-presigned-url-must-not-leak";
        SpringAiAgentChatService service = service(mock(ChatModelFactory.class));
        SseEmitter emitter = mock(SseEmitter.class);
        String streamId = "stream-http-1";
        AgentChatTask task = AgentChatTask.builder().build();
        AgentSseBridge bridge = new AgentSseBridge(emitter, streamId);
        WebClientResponseException exception = new WebClientResponseException(
                502,
                "Bad Gateway",
                HttpHeaders.EMPTY,
                sentinel.getBytes(StandardCharsets.UTF_8),
                StandardCharsets.UTF_8);
        Logger logger = (Logger) LoggerFactory.getLogger(SpringAiAgentChatService.class);
        ListAppender<ILoggingEvent> appender = capture(logger);

        try (MockedStatic<SseEmitterUtil> sse = mockStatic(SseEmitterUtil.class)) {
            ReflectionTestUtils.invokeMethod(
                    service, "handleStreamError", exception, emitter, streamId, task, bridge);

            sse.verify(() -> SseEmitterUtil.completeWithError(
                    emitter, "Failed to process chat request (streamId: " + streamId + ")"));
        } finally {
            stopCapture(logger, appender);
        }

        assertThat(messages(appender))
                .anySatisfy(message -> assertThat(message)
                        .contains(
                                "streamId=" + streamId,
                                "httpStatus=502",
                                "errorType=WebClientResponseException"))
                .noneSatisfy(message -> assertThat(message)
                        .contains(sentinel, "responseBody", "presigned-url"));
    }

    private static SpringAiAgentChatService service(ChatModelFactory chatModelFactory) {
        return new SpringAiAgentChatService(
                chatModelFactory,
                mock(AgentToolCallbackResolver.class),
                mock(ChatRecordModelService.class),
                mock(ChatDataService.class),
                mock(AgentMemoryRuntimeService.class),
                Runnable::run);
    }

    private static ListAppender<ILoggingEvent> capture(Logger logger) {
        ListAppender<ILoggingEvent> appender = new ListAppender<>();
        appender.start();
        logger.addAppender(appender);
        return appender;
    }

    private static void stopCapture(Logger logger, ListAppender<ILoggingEvent> appender) {
        logger.detachAppender(appender);
        appender.stop();
    }

    private static List<String> messages(ListAppender<ILoggingEvent> appender) {
        return appender.list.stream().map(ILoggingEvent::getFormattedMessage).toList();
    }
}
