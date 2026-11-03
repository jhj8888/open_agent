package com.iflytek.astron.console.toolkit.service.workflow;

import ch.qos.logback.classic.Logger;
import ch.qos.logback.classic.spi.ILoggingEvent;
import ch.qos.logback.core.read.ListAppender;
import com.alibaba.fastjson2.JSON;
import com.alibaba.fastjson2.JSONArray;
import com.alibaba.fastjson2.JSONObject;
import com.iflytek.astron.console.commons.entity.workflow.Workflow;
import com.iflytek.astron.console.commons.exception.BusinessException;
import com.iflytek.astron.console.toolkit.config.properties.ApiUrl;
import com.iflytek.astron.console.toolkit.entity.biz.workflow.node.BizInputOutput;
import com.iflytek.astron.console.toolkit.entity.biz.workflow.node.BizSchema;
import com.iflytek.astron.console.toolkit.entity.dto.WorkflowReq;
import com.iflytek.astron.console.toolkit.entity.core.workflow.node.InputOutput;
import com.iflytek.astron.console.toolkit.util.OkHttpUtil;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.MockedStatic;
import org.slf4j.LoggerFactory;
import org.springframework.test.util.ReflectionTestUtils;

import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.mockStatic;

class WorkflowServiceProtocolBoundaryTest {

    private static final String INTERNAL_KEY =
            "0123456789abcdef0123456789abcdef";

    private WorkflowService workflowService;

    @BeforeEach
    void setUp() {
        workflowService = new WorkflowService();
        ReflectionTestUtils.setField(
                workflowService, "workflowInternalApiKey", INTERNAL_KEY);
    }

    @Test
    void outputCopyDefaultsMissingRequiredToFalseAndPreservesTrue() {
        List<InputOutput> outputs = new ArrayList<>();

        ReflectionTestUtils.invokeMethod(
                workflowService,
                "outputCopy",
                List.of(output("optional", null), output("required", Boolean.TRUE)),
                outputs);

        assertThat(outputs).extracting(InputOutput::getRequired).containsExactly(Boolean.FALSE, Boolean.TRUE);

        JSONArray serializedOutputs = JSON.parseArray(JSON.toJSONString(outputs));
        assertThat(serializedOutputs.getJSONObject(0).containsKey("required")).isTrue();
        assertThat(serializedOutputs.getJSONObject(0).getBoolean("required")).isFalse();
        assertThat(serializedOutputs.getJSONObject(1).getBoolean("required")).isTrue();
    }

    @Test
    void protocolUpdateLogContainsSafeSummaryWithoutProtocolBody() {
        String url = "http://workflow/workflow/v1/protocol/update/flow-1";
        String flowId = "flow-1";
        String body = "{\"modelApiKey\":\"secret-api-key\",\"prompt\":\"敏感提示词\"}";
        JSONObject protocolJson = new JSONObject().fluentPut(
                "data",
                new JSONObject()
                        .fluentPut("nodes", new JSONArray(List.of(new JSONObject(), new JSONObject())))
                        .fluentPut("edges", new JSONArray(List.of(new JSONObject()))));

        Logger logger = (Logger) LoggerFactory.getLogger(WorkflowService.class);
        ListAppender<ILoggingEvent> appender = new ListAppender<>();
        appender.start();
        logger.addAppender(appender);
        try {
            ReflectionTestUtils.invokeMethod(
                    workflowService, "logWorkflowProtocolUpdate", url, flowId, body, protocolJson);
        } finally {
            logger.detachAppender(appender);
            appender.stop();
        }

        assertThat(appender.list).hasSize(1);
        String message = appender.list.getFirst().getFormattedMessage();
        assertThat(message)
                .contains("url = " + url)
                .contains("flowId = " + flowId)
                .contains("bodyBytes = " + body.getBytes(StandardCharsets.UTF_8).length)
                .contains("nodeCount = 2")
                .contains("edgeCount = 1")
                .doesNotContain(body)
                .doesNotContain("secret-api-key")
                .doesNotContain("敏感提示词");
    }

    @Test
    void protocolAddLogsDoNotExposeRequestOrResponsePayloads() {
        String sentinel = "protocol-add-secret-that-must-not-be-logged";
        ApiUrl apiUrl = new ApiUrl();
        apiUrl.setWorkflow("http://workflow");
        ReflectionTestUtils.setField(workflowService, "apiUrl", apiUrl);
        WorkflowReq request = new WorkflowReq();
        request.setAppId("app-1");
        request.setName("workflow-name");
        request.setDescription(sentinel);
        Logger logger = (Logger) LoggerFactory.getLogger(WorkflowService.class);
        ListAppender<ILoggingEvent> appender = new ListAppender<>();
        appender.start();
        logger.addAppender(appender);
        try (MockedStatic<OkHttpUtil> okHttp = mockStatic(OkHttpUtil.class)) {
            okHttp.when(() -> OkHttpUtil.post(
                    anyString(),
                    org.mockito.ArgumentMatchers.anyMap(),
                    anyString()))
                    .thenReturn("{\"code\":0,\"message\":\""
                            + sentinel
                            + "\",\"data\":{\"flow_id\":\"flow-1\",\"echo\":\""
                            + sentinel
                            + "\"}}");

            workflowService.callProtocolAdd(request);
        } finally {
            logger.detachAppender(appender);
            appender.stop();
        }

        assertThat(appender.list)
                .extracting(ILoggingEvent::getFormattedMessage)
                .anySatisfy(message -> assertThat(message)
                        .contains("workflow protocol add request")
                        .contains("bodyBytes = "))
                .anySatisfy(message -> assertThat(message)
                        .contains("workflow protocol add response")
                        .contains("statusCode = 0"))
                .noneSatisfy(message -> assertThat(message).contains(sentinel));
    }

    @Test
    void buildEventLogsOnlySizeAndExtractsStatusWithoutErrorDetail() {
        String sentinel = "build-dsl-secret-and-presigned-url-must-not-leak";
        String event = new JSONObject()
                .fluentPut("message", "500:" + sentinel)
                .fluentPut("dsl", sentinel)
                .toJSONString();
        Logger logger = (Logger) LoggerFactory.getLogger(WorkflowService.class);
        ListAppender<ILoggingEvent> appender = new ListAppender<>();
        appender.start();
        logger.addAppender(appender);

        Integer status;
        try {
            status = ReflectionTestUtils.invokeMethod(
                    workflowService,
                    "parseBuildEventStatus",
                    event,
                    "http://workflow/workflow/v1/protocol/build/flow-1",
                    "flow-1");
        } finally {
            logger.detachAppender(appender);
            appender.stop();
        }

        assertThat(status).isEqualTo(500);
        assertThat(appender.list)
                .extracting(ILoggingEvent::getFormattedMessage)
                .anySatisfy(message -> assertThat(message)
                        .contains("flowId = flow-1")
                        .contains("eventBytes = " + event.getBytes(StandardCharsets.UTF_8).length))
                .noneSatisfy(message -> assertThat(message).contains(sentinel));
    }

    @Test
    void malformedBuildEventFailsClosedWithoutLoggingOrReturningPayload() {
        String sentinel = "malformed-build-event-secret-must-not-leak";
        String event = "{\"message\":\"" + sentinel;
        Logger logger = (Logger) LoggerFactory.getLogger(WorkflowService.class);
        ListAppender<ILoggingEvent> appender = new ListAppender<>();
        appender.start();
        logger.addAppender(appender);

        try {
            assertThatThrownBy(() -> ReflectionTestUtils.invokeMethod(
                    workflowService,
                    "parseBuildEventStatus",
                    event,
                    "http://workflow/workflow/v1/protocol/build/flow-1",
                    "flow-1"))
                    .isInstanceOf(BusinessException.class)
                    .hasMessageNotContaining(sentinel);
        } finally {
            logger.detachAppender(appender);
            appender.stop();
        }

        assertThat(appender.list)
                .extracting(ILoggingEvent::getFormattedMessage)
                .anySatisfy(message -> assertThat(message)
                        .contains("event parse failed", "errorType = "))
                .noneSatisfy(message -> assertThat(message).contains(sentinel));
    }

    @Test
    void advancedConfigFailureLogsOnlySizesAndErrorType() {
        String sentinel = "advanced-config-api-key-must-not-leak";
        Workflow workflow = new Workflow();
        workflow.setAdvancedConfig("{\"apiKey\":\"" + sentinel + "\"");
        WorkflowReq request = new WorkflowReq();
        request.setAdvancedConfig(Map.of("apiKey", sentinel));
        Logger logger = (Logger) LoggerFactory.getLogger(WorkflowService.class);
        ListAppender<ILoggingEvent> appender = new ListAppender<>();
        appender.start();
        logger.addAppender(appender);

        try {
            assertThatThrownBy(() -> ReflectionTestUtils.invokeMethod(
                    workflowService, "mergeAdvancedConfigSafe", request, workflow))
                    .isInstanceOf(BusinessException.class)
                    .hasMessageNotContaining(sentinel);
        } finally {
            logger.detachAppender(appender);
            appender.stop();
        }

        assertThat(appender.list)
                .extracting(ILoggingEvent::getFormattedMessage)
                .anySatisfy(message -> assertThat(message)
                        .contains("originalBytes = ", "updateBytes = ", "errorType = "))
                .noneSatisfy(message -> assertThat(message).contains(sentinel));
    }

    private static BizInputOutput output(String name, Boolean required) {
        BizSchema schema = new BizSchema();
        schema.setType("string");

        BizInputOutput output = new BizInputOutput();
        output.setName(name);
        output.setRequired(required);
        output.setSchema(schema);
        return output;
    }
}
