package com.iflytek.astron.console.hub.service.chat.impl;

import static org.assertj.core.api.Assertions.assertThat;

import ch.qos.logback.classic.Logger;
import ch.qos.logback.classic.spi.ILoggingEvent;
import ch.qos.logback.core.read.ListAppender;
import com.alibaba.fastjson2.JSONObject;
import java.nio.charset.StandardCharsets;
import java.util.List;
import org.junit.jupiter.api.Test;
import org.slf4j.LoggerFactory;
import org.springframework.test.util.ReflectionTestUtils;

class BotChatServiceImplSecurityTest {

    @Test
    void invalidSavedSkillsLogDoesNotExposeHistoricalDownloadUrl() {
        String sentinel = "https://storage.example/skill?X-Amz-Signature=must-not-leak";
        String malformedSkills = "[{\"downloadUrl\":\"" + sentinel;
        BotChatServiceImpl service = new BotChatServiceImpl();
        Logger logger = (Logger) LoggerFactory.getLogger(BotChatServiceImpl.class);
        ListAppender<ILoggingEvent> appender = new ListAppender<>();
        appender.start();
        logger.addAppender(appender);

        List<JSONObject> result;
        try {
            result = ReflectionTestUtils.invokeMethod(service, "enrichBotSkills", malformedSkills);
        } finally {
            logger.detachAppender(appender);
            appender.stop();
        }

        assertThat(result).isEmpty();
        assertThat(appender.list)
                .extracting(ILoggingEvent::getFormattedMessage)
                .anySatisfy(message -> assertThat(message)
                        .contains(
                                "bodyBytes="
                                        + malformedSkills.getBytes(StandardCharsets.UTF_8).length,
                                "errorType="))
                .noneSatisfy(message -> assertThat(message)
                        .contains(sentinel, "X-Amz-Signature", "storage.example"));
    }
}
