package com.iflytek.astron.console.toolkit.service.extra;

import ch.qos.logback.classic.Logger;
import ch.qos.logback.classic.spi.ILoggingEvent;
import ch.qos.logback.core.read.ListAppender;
import com.iflytek.astron.console.commons.exception.BusinessException;
import com.iflytek.astron.console.toolkit.config.properties.ApiUrl;
import com.iflytek.astron.console.toolkit.config.properties.CommonConfig;
import com.iflytek.astron.console.toolkit.entity.biz.external.app.AkSk;
import com.iflytek.astron.console.toolkit.tool.CommonTool;
import com.iflytek.astron.console.toolkit.tool.http.HeaderAuthHttpTool;
import com.iflytek.astron.console.toolkit.util.RedisUtil;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.*;
import org.mockito.junit.jupiter.MockitoExtension;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.config.ConfigurableListableBeanFactory;
import org.springframework.test.util.ReflectionTestUtils;

import java.io.IOException;
import static org.assertj.core.api.Assertions.*;
import static org.mockito.Mockito.*;

/**
 * Unit tests for {@link AppService}.
 */
@ExtendWith(MockitoExtension.class)
class AppServiceTest {

    @BeforeAll
    static void initializeCommonToolDependencies() throws Exception {
        ConfigurableListableBeanFactory fakeBeanFactory =
                mock(ConfigurableListableBeanFactory.class, withSettings()
                        .defaultAnswer(invocation -> {
                            if ("getBean".equals(invocation.getMethod().getName())) {
                                Class<?> type = invocation.getArgument(0);
                                return Mockito.mock(type);
                            }
                            return RETURNS_DEFAULTS.answer(invocation);
                        }));
        Class<?> springUtils =
                Class.forName("com.iflytek.astron.console.toolkit.util.SpringUtils");
        var beanFactoryField = springUtils.getDeclaredField("beanFactory");
        beanFactoryField.setAccessible(true);
        beanFactoryField.set(null, fakeBeanFactory);
    }

    @InjectMocks
    private AppService appService;

    @Mock
    private ApiUrl apiUrl;
    @Mock
    private RedisUtil redisUtil;
    // RedisTemplate not directly used, keep default Mock
    @Mock
    private CommonConfig commonConfig;
    // Add this import at the top

    @Test
    @DisplayName("getAkSk - Remote returns empty array: Should throw BusinessException (containing APPID hint)")
    void getAkSk_shouldThrow_whenArrayEmpty() throws Exception {
        String appId = "APP-5";

        // Take the "remote branch": Cache miss and not a "special APPID"
        when(redisUtil.get("app_detail_cache:" + appId)).thenReturn(null);
        when(commonConfig.getAppId()).thenReturn("NOT-SPECIAL");

        // URL and auth parameters must be stubbed to avoid null/key/APP-5
        when(apiUrl.getAppUrl()).thenReturn("http://api");
        when(apiUrl.getApiKey()).thenReturn("ak");
        when(apiUrl.getApiSecret()).thenReturn("sk");

        // Static mock: HTTP returns placeholder response; parsing returns empty array "[]"
        try (MockedStatic<HeaderAuthHttpTool> http = mockStatic(HeaderAuthHttpTool.class);
                MockedStatic<CommonTool> common = mockStatic(CommonTool.class)) {

            http.when(() -> HeaderAuthHttpTool.tenantGet("http://api/key/" + appId, "ak", "sk"))
                    .thenReturn("resp");
            common.when(() -> CommonTool.checkSystemCallResponse("resp"))
                    .thenReturn("[]");

            assertThatThrownBy(() -> appService.getAkSk(appId))
                    .isInstanceOf(BusinessException.class)
                    .hasMessageContaining("common.response.failed");

            // Interaction verification (improve PIT killing power)
            verify(redisUtil).get("app_detail_cache:" + appId);
            http.verify(() -> HeaderAuthHttpTool.tenantGet("http://api/key/" + appId, "ak", "sk"));
            common.verify(() -> CommonTool.checkSystemCallResponse("resp"));
        }
    }

    // ================= getAkSk: HTTP throws checked exception Wrapped as RuntimeException
    // =================

    @Test
    @DisplayName("getAkSk - HeaderAuthHttpTool.tenantGet throws IOException: Should wrap as RuntimeException with cause")
    void getAkSk_shouldWrapHttpException() {
        String appId = "APP-6";
        when(redisUtil.get("app_detail_cache:" + appId)).thenReturn(null);
        when(apiUrl.getAppUrl()).thenReturn("http://api");
        when(apiUrl.getApiKey()).thenReturn("ak");
        when(apiUrl.getApiSecret()).thenReturn("sk");

        try (MockedStatic<HeaderAuthHttpTool> http = mockStatic(HeaderAuthHttpTool.class)) {
            http.when(() -> HeaderAuthHttpTool.tenantGet("http://api/key/" + appId, "ak", "sk"))
                    .thenThrow(new IOException("net down"));

            assertThatThrownBy(() -> appService.getAkSk(appId))
                    .isInstanceOf(RuntimeException.class)
                    .hasCauseInstanceOf(IOException.class)
                    .hasRootCauseMessage("net down");
        }
    }

    @Test
    void parsedTenantCredentialIsNotWrittenToLogs() {
        String apiKey = "api-key-sentinel-must-not-be-logged";
        String apiSecret = "api-secret-sentinel-must-not-be-logged";
        String response = "{\"code\":0,\"data\":[{\"api_key\":\""
                + apiKey
                + "\",\"api_secret\":\""
                + apiSecret
                + "\"}]}";
        Logger logger = (Logger) LoggerFactory.getLogger(AppService.class);
        ListAppender<ILoggingEvent> appender = new ListAppender<>();
        appender.start();
        logger.addAppender(appender);

        AkSk credential;
        try {
            credential = ReflectionTestUtils.invokeMethod(
                    appService,
                    "parseRemoteCredential",
                    response,
                    "APP-7",
                    "uncached");
        } finally {
            logger.detachAppender(appender);
            appender.stop();
        }

        assertThat(credential.getApiKey()).isEqualTo(apiKey);
        assertThat(credential.getApiSecret()).isEqualTo(apiSecret);
        assertThat(appender.list)
                .extracting(ILoggingEvent::getFormattedMessage)
                .singleElement()
                .asString()
                .contains("APP credential query succeeded")
                .contains("appId=APP-7")
                .contains("mode=uncached")
                .doesNotContain(apiKey)
                .doesNotContain(apiSecret);
    }
}
