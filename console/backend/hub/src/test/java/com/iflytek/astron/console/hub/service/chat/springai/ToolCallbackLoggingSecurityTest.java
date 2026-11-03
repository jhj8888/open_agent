package com.iflytek.astron.console.hub.service.chat.springai;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

import ch.qos.logback.classic.Logger;
import ch.qos.logback.classic.spi.ILoggingEvent;
import ch.qos.logback.core.read.ListAppender;
import com.alibaba.fastjson2.JSON;
import com.alibaba.fastjson2.JSONObject;
import com.iflytek.astron.console.hub.service.ManagedWebSearchService;
import com.iflytek.astron.console.hub.service.ManagedWebSearchService.SearchAugmentation;
import com.iflytek.astron.console.hub.service.McpRuntimeToolService;
import com.iflytek.astron.console.hub.service.McpRuntimeToolService.McpRuntimeTool;
import com.iflytek.astron.console.toolkit.entity.tool.AgentToolDefinition;
import com.iflytek.astron.console.toolkit.entity.workflow.AgentWorkflowDefinition;
import com.iflytek.astron.console.toolkit.service.tool.AgentToolRuntimeService;
import com.iflytek.astron.console.toolkit.service.workflow.AgentWorkflowRuntimeService;
import java.io.IOException;
import java.util.ArrayList;
import java.util.List;
import org.junit.jupiter.api.Test;
import org.slf4j.LoggerFactory;
import org.springframework.ai.tool.ToolCallback;

class ToolCallbackLoggingSecurityTest {

    @Test
    void malformedToolInputsAndMcpFailuresNeverExposePayloadsOrServerUrls() throws Exception {
        String sentinel = "https://service.example/path?token=must-not-leak";
        List<LoggerCapture> captures = List.of(
                capture(LinkToolCallbackFactory.class),
                capture(McpToolCallbackFactory.class),
                capture(WorkflowToolCallbackFactory.class),
                capture(WebSearchToolCallback.class),
                capture(CurrentTimeToolCallback.class));
        try {
            assertThat(callLinkTool("{\"secret\":\"" + sentinel)).isEqualTo("link-ok");

            ChatToolContext mcpContext = new ChatToolContext("user-1");
            String mcpResult = callFailingMcpTool(sentinel, mcpContext);
            assertThat(mcpResult).isEqualTo("MCP tool call failed.");
            assertThat(JSON.toJSONString(mcpContext.drainTrace()))
                    .doesNotContain(sentinel, "serverUrl", "token=");

            assertThat(callWorkflowTool("{\"secret\":\"" + sentinel))
                    .isEqualTo("workflow-ok");

            ManagedWebSearchService search = mock(ManagedWebSearchService.class);
            when(search.search(eq(sentinel), eq("user-1")))
                    .thenReturn(new SearchAugmentation("", "", true, sentinel));
            WebSearchToolCallback webSearch =
                    new WebSearchToolCallback(search, new ChatToolContext("user-1"));
            assertThat(webSearch.call("{\"query\":\"" + sentinel))
                    .isEqualTo("No query provided.");
            assertThat(webSearch.call(new JSONObject().fluentPut("query", sentinel).toJSONString()))
                    .isEqualTo("Web search failed.");

            assertThat(new CurrentTimeToolCallback().call("{\"timezone\":\"" + sentinel))
                    .isNotBlank();
        } finally {
            captures.forEach(LoggerCapture::close);
        }

        List<String> messages = captures.stream()
                .flatMap(capture -> capture.messages().stream())
                .toList();
        assertThat(messages)
                .anySatisfy(message -> assertThat(message)
                        .contains("inputBytes=", "errorType="))
                .noneSatisfy(message -> assertThat(message)
                        .contains(sentinel, "token=must-not-leak", "service.example"));
    }

    private static String callLinkTool(String malformedInput) {
        AgentToolRuntimeService runtime = mock(AgentToolRuntimeService.class);
        AgentToolDefinition definition = AgentToolDefinition.builder()
                .toolId("tool@safe")
                .functionName("safe_link_tool")
                .description("safe")
                .inputSchema("{\"type\":\"object\"}")
                .build();
        when(runtime.resolveTools(List.of("tool@safe"))).thenReturn(List.of(definition));
        when(runtime.runTool(eq(definition), any())).thenReturn("link-ok");
        ToolCallback callback = new LinkToolCallbackFactory(runtime)
                .build(List.of("tool@safe"), new ChatToolContext("user-1"))
                .getFirst();
        return callback.call(malformedInput);
    }

    private static String callFailingMcpTool(String sentinel, ChatToolContext context)
            throws Exception {
        McpRuntimeToolService runtime = mock(McpRuntimeToolService.class);
        McpRuntimeTool tool = new McpRuntimeTool(
                "mcp_safe",
                "server-1",
                sentinel,
                "safe_tool",
                "safe",
                new JSONObject());
        when(runtime.listTools(List.of("server-1"))).thenReturn(List.of(tool));
        when(runtime.callTool(eq(tool), any())).thenThrow(new IOException(sentinel));
        ToolCallback callback = new McpToolCallbackFactory(runtime)
                .build(List.of("server-1"), context)
                .getFirst();
        return callback.call("{\"secret\":\"" + sentinel);
    }

    private static String callWorkflowTool(String malformedInput) {
        AgentWorkflowRuntimeService runtime = mock(AgentWorkflowRuntimeService.class);
        AgentWorkflowDefinition definition = AgentWorkflowDefinition.builder()
                .flowId("flow-safe")
                .functionName("workflow_safe")
                .description("safe")
                .inputSchema("{\"type\":\"object\"}")
                .build();
        when(runtime.resolveWorkflows(List.of("flow-safe"))).thenReturn(List.of(definition));
        when(runtime.runWorkflow(eq(definition), eq("user-1"), any()))
                .thenReturn("workflow-ok");
        ToolCallback callback = new WorkflowToolCallbackFactory(runtime)
                .build(List.of("flow-safe"), new ChatToolContext("user-1"))
                .getFirst();
        return callback.call(malformedInput);
    }

    private static LoggerCapture capture(Class<?> type) {
        Logger logger = (Logger) LoggerFactory.getLogger(type);
        ListAppender<ILoggingEvent> appender = new ListAppender<>();
        appender.start();
        logger.addAppender(appender);
        return new LoggerCapture(logger, appender);
    }

    private record LoggerCapture(Logger logger, ListAppender<ILoggingEvent> appender) {
        private void close() {
            logger.detachAppender(appender);
            appender.stop();
        }

        private List<String> messages() {
            List<String> result = new ArrayList<>();
            for (ILoggingEvent event : appender.list) {
                result.add(event.getFormattedMessage());
            }
            return result;
        }
    }
}
