package com.iflytek.astron.console.toolkit.service.extra;

import ch.qos.logback.classic.Logger;
import ch.qos.logback.classic.spi.ILoggingEvent;
import ch.qos.logback.core.read.ListAppender;
import com.alibaba.fastjson2.JSONObject;
import com.iflytek.astron.console.commons.exception.BusinessException;
import com.iflytek.astron.console.toolkit.config.properties.ApiUrl;
import com.iflytek.astron.console.toolkit.config.properties.CommonConfig;
import com.iflytek.astron.console.toolkit.entity.core.workflow.FlowProtocol;
import com.iflytek.astron.console.toolkit.entity.enumVo.DBOperateEnum;
import com.iflytek.astron.console.toolkit.entity.enumVo.DBTableEnvEnum;
import com.iflytek.astron.console.toolkit.util.OkHttpUtil;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.MockedStatic;
import org.slf4j.LoggerFactory;
import org.springframework.test.util.ReflectionTestUtils;

import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.anyMap;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.mockStatic;

class CoreSystemServiceComparisonTest {

    private static final String INTERNAL_KEY =
            "0123456789abcdef0123456789abcdef";

    private CoreSystemService coreSystemService;

    @BeforeEach
    void setUp() {
        coreSystemService = new CoreSystemService();
        ApiUrl apiUrl = new ApiUrl();
        apiUrl.setWorkflow("http://core");
        apiUrl.setSparkDB("http://spark-db");
        apiUrl.setTenantId("tenant");
        ReflectionTestUtils.setField(coreSystemService, "apiUrl", apiUrl);
        CommonConfig commonConfig = new CommonConfig();
        commonConfig.setAppId("console-app");
        ReflectionTestUtils.setField(coreSystemService, "commonConfig", commonConfig);
        ReflectionTestUtils.setField(
                coreSystemService, "workflowInternalApiKey", INTERNAL_KEY);
    }

    @Test
    void getComparisonReturnsExactProtocolData() {
        try (MockedStatic<OkHttpUtil> okHttp = mockStatic(OkHttpUtil.class)) {
            okHttp.when(() -> OkHttpUtil.post(
                    "http://core/workflow/v1/protocol/compare/get",
                    Map.of("X-Workflow-Internal-Key", INTERNAL_KEY),
                    "{\"flow_id\":\"flow-1\",\"version\":\"cmp-1\"}"))
                    .thenReturn("{\"code\":0,\"message\":\"success\","
                            + "\"data\":{\"data\":{\"nodes\":[],\"edges\":[]}}}");

            JSONObject snapshot = coreSystemService.getComparison("flow-1", "cmp-1");

            assertThat(snapshot.getJSONObject("data").getJSONArray("nodes")).isEmpty();
        }
    }

    @Test
    void getComparisonFailsClosedForMissingOrInvalidData() {
        try (MockedStatic<OkHttpUtil> okHttp = mockStatic(OkHttpUtil.class)) {
            okHttp.when(() -> OkHttpUtil.post(
                    "http://core/workflow/v1/protocol/compare/get",
                    Map.of("X-Workflow-Internal-Key", INTERNAL_KEY),
                    "{\"flow_id\":\"flow-1\",\"version\":\"missing\"}"))
                    .thenReturn("{\"code\":1001,\"message\":\"not found\"}");

            assertThatThrownBy(() -> coreSystemService.getComparison("flow-1", "missing"))
                    .isInstanceOf(BusinessException.class);
        }
    }

    @Test
    void addComparisonsLogDoesNotExposeProtocolBody() {
        String sentinel = "comparison-secret-that-must-not-be-logged";
        FlowProtocol protocol = new FlowProtocol();
        protocol.setDescription(sentinel);
        Logger logger = (Logger) LoggerFactory.getLogger(CoreSystemService.class);
        ListAppender<ILoggingEvent> appender = new ListAppender<>();
        appender.start();
        logger.addAppender(appender);
        try (MockedStatic<OkHttpUtil> okHttp = mockStatic(OkHttpUtil.class)) {
            okHttp.when(() -> OkHttpUtil.post(
                    org.mockito.ArgumentMatchers.anyString(),
                    anyMap(),
                    org.mockito.ArgumentMatchers.anyString()))
                    .thenReturn("{\"code\":0,\"message\":\""
                            + sentinel
                            + "\",\"data\":{\"echo\":\""
                            + sentinel
                            + "\"}}");

            coreSystemService.addComparisons(protocol, "flow-1", "cmp-1");
        } finally {
            logger.detachAppender(appender);
            appender.stop();
        }

        assertThat(appender.list)
                .extracting(ILoggingEvent::getFormattedMessage)
                .anySatisfy(message -> assertThat(message)
                        .contains("flowId = flow-1")
                        .contains("bodyBytes = ")
                        .doesNotContain(sentinel))
                .noneSatisfy(message -> assertThat(message).contains(sentinel));
    }

    @Test
    void databaseAndComparisonLogsDoNotExposeRequestOrResponsePayloads() {
        String sentinel = "database-secret-and-signed-url-must-not-leak";
        Logger logger = (Logger) LoggerFactory.getLogger(CoreSystemService.class);
        ListAppender<ILoggingEvent> appender = new ListAppender<>();
        appender.start();
        logger.addAppender(appender);
        try (MockedStatic<OkHttpUtil> okHttp = mockStatic(OkHttpUtil.class)) {
            okHttp.when(() -> OkHttpUtil.post(anyString(), anyMap(), anyString()))
                    .thenAnswer(invocation -> invocation.getArgument(0, String.class).contains("create_database")
                            ? "{\"code\":0,\"message\":\"" + sentinel
                                    + "\",\"data\":{\"database_id\":7000000000}}"
                            : "{\"code\":0,\"message\":\"" + sentinel + "\",\"data\":{}}");
            okHttp.when(() -> OkHttpUtil.delete(anyString(), anyMap(), anyString()))
                    .thenReturn("{\"code\":0,\"message\":\"" + sentinel + "\",\"data\":{}}");

            assertThat(coreSystemService.createDatabase(sentinel, "uid", 3L, sentinel))
                    .isEqualTo(7_000_000_000L);
            coreSystemService.execDDL("CREATE TABLE " + sentinel, "uid", 3L, 7L);
            assertThat(coreSystemService.execDML(
                    "INSERT " + sentinel,
                    "uid",
                    3L,
                    7L,
                    DBOperateEnum.INSERT.getCode(),
                    DBTableEnvEnum.TEST.getCode()))
                    .isNull();
            coreSystemService.deleteComparisons("flow-1", "cmp-1");
        } finally {
            logger.detachAppender(appender);
            appender.stop();
        }

        assertThat(appender.list)
                .extracting(ILoggingEvent::getFormattedMessage)
                .anySatisfy(message -> assertThat(message)
                        .contains("create database", "bodyBytes = "))
                .anySatisfy(message -> assertThat(message)
                        .contains("exec ddl", "databaseId = 7", "bodyBytes = "))
                .anySatisfy(message -> assertThat(message)
                        .contains("exec dml", "databaseId = 7", "bodyBytes = "))
                .noneSatisfy(message -> assertThat(message).contains(sentinel));
    }

    @Test
    void upstreamDatabaseFailureDoesNotEscapeResponseMessageOrBody() {
        String sentinel = "https://storage.example/file?X-Amz-Signature=must-not-leak";
        try (MockedStatic<OkHttpUtil> okHttp = mockStatic(OkHttpUtil.class)) {
            okHttp.when(() -> OkHttpUtil.post(anyString(), anyMap(), anyString()))
                    .thenReturn("{\"code\":500,\"message\":\"" + sentinel + "\"}");

            assertThatThrownBy(() -> coreSystemService.execDDL(sentinel, "uid", null, 7L))
                    .isInstanceOf(BusinessException.class)
                    .hasMessageNotContaining(sentinel)
                    .hasMessageNotContaining("storage.example")
                    .hasMessageNotContaining("X-Amz-Signature");
        }
    }
}
