package com.iflytek.astron.console.toolkit.tool;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.mockStatic;

import ch.qos.logback.classic.Logger;
import ch.qos.logback.classic.spi.ILoggingEvent;
import ch.qos.logback.core.read.ListAppender;
import com.iflytek.astron.console.commons.config.JwtClaimsFilter;
import com.iflytek.astron.console.commons.entity.workflow.Workflow;
import com.iflytek.astron.console.commons.exception.BusinessException;
import com.iflytek.astron.console.commons.mapper.UserLangChainInfoMapper;
import com.iflytek.astron.console.commons.service.bot.BotMarketDataService;
import com.iflytek.astron.console.toolkit.config.properties.BizConfig;
import com.iflytek.astron.console.toolkit.mapper.bot.SparkBotMapper;
import com.iflytek.astron.console.toolkit.mapper.database.DbInfoMapper;
import com.iflytek.astron.console.toolkit.mapper.database.DbTableMapper;
import com.iflytek.astron.console.toolkit.mapper.group.GroupVisibilityMapper;
import com.iflytek.astron.console.toolkit.mapper.repo.RepoMapper;
import com.iflytek.astron.console.toolkit.mapper.workflow.WorkflowMapper;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import org.mockito.MockedStatic;
import org.slf4j.LoggerFactory;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.web.context.request.RequestContextHolder;
import org.springframework.web.context.request.ServletRequestAttributes;

class DataPermissionCheckToolLoggingTest {

    @AfterEach
    void tearDown() {
        RequestContextHolder.resetRequestAttributes();
    }

    @Test
    void deniedResourceLogNeverSerializesWorkflowProtocolOrCredentials() {
        MockHttpServletRequest request = new MockHttpServletRequest();
        request.setAttribute(JwtClaimsFilter.USER_ID_ATTRIBUTE, "attacker");
        request.addHeader("space-id", "7");
        RequestContextHolder.setRequestAttributes(new ServletRequestAttributes(request));
        BizConfig bizConfig = new BizConfig();
        bizConfig.setAdminUid("admin");
        DataPermissionCheckTool tool = new DataPermissionCheckTool(
                mock(GroupVisibilityMapper.class),
                bizConfig,
                mock(RepoMapper.class),
                mock(SparkBotMapper.class),
                mock(WorkflowMapper.class),
                mock(DbInfoMapper.class),
                mock(DbTableMapper.class),
                mock(UserLangChainInfoMapper.class),
                mock(BotMarketDataService.class));
        String sentinel = "protocol-secret-and-presigned-url-must-not-be-logged";
        Workflow workflow = new Workflow();
        workflow.setUid("owner");
        workflow.setSpaceId(8L);
        workflow.setData("{\"token\":\"" + sentinel + "\"}");
        Logger logger = (Logger) LoggerFactory.getLogger(DataPermissionCheckTool.class);
        ListAppender<ILoggingEvent> appender = new ListAppender<>();
        appender.start();
        logger.addAppender(appender);
        try {
            assertThatThrownBy(() -> tool.checkWorkflowVisible(workflow, 7L))
                    .isInstanceOf(BusinessException.class);
        } finally {
            logger.detachAppender(appender);
            appender.stop();
        }

        assertThat(appender.list)
                .extracting(ILoggingEvent::getFormattedMessage)
                .singleElement()
                .asString()
                .contains(
                        "action=checkWorkflowVisible",
                        "uid=attacker",
                        "currentSpaceId=7",
                        "resourceType=Workflow")
                .doesNotContain(sentinel, "resource=Workflow(");
    }

    @Test
    void forgedSameSpaceContextCannotReadPrivateWorkflowWithoutMembership() {
        BizConfig bizConfig = new BizConfig();
        bizConfig.setAdminUid("admin");
        DataPermissionCheckTool tool = new DataPermissionCheckTool(
                mock(GroupVisibilityMapper.class),
                bizConfig,
                mock(RepoMapper.class),
                mock(SparkBotMapper.class),
                mock(WorkflowMapper.class),
                mock(DbInfoMapper.class),
                mock(DbTableMapper.class),
                mock(UserLangChainInfoMapper.class),
                mock(BotMarketDataService.class));
        Workflow workflow = new Workflow();
        workflow.setUid("space-owner");
        workflow.setSpaceId(7L);
        workflow.setIsPublic(Boolean.FALSE);

        try (MockedStatic<com.iflytek.astron.console.commons.util.space.SpaceInfoUtil> space =
                mockStatic(com.iflytek.astron.console.commons.util.space.SpaceInfoUtil.class)) {
            space.when(com.iflytek.astron.console.commons.util.space.SpaceInfoUtil::getSpaceId)
                    .thenReturn(7L);
            space.when(com.iflytek.astron.console.commons.util.space.SpaceInfoUtil::checkUserBelongSpace)
                    .thenReturn(false);
            MockHttpServletRequest request = new MockHttpServletRequest();
            request.setAttribute(JwtClaimsFilter.USER_ID_ATTRIBUTE, "outsider");
            RequestContextHolder.setRequestAttributes(new ServletRequestAttributes(request));

            assertThatThrownBy(() -> tool.checkWorkflowVisible(workflow, 7L))
                    .isInstanceOf(BusinessException.class);
            assertThatThrownBy(() -> tool.checkWorkflowVisibleForDetail(workflow, 7L))
                    .isInstanceOf(BusinessException.class);
            assertThatThrownBy(() -> tool.checkWorkflowBelong(workflow, 7L))
                    .isInstanceOf(BusinessException.class);
        }
    }

    @Test
    void publicVisibilityNeverGrantsPersonalWorkflowWriteAccess() {
        MockHttpServletRequest request = new MockHttpServletRequest();
        request.setAttribute(JwtClaimsFilter.USER_ID_ATTRIBUTE, "attacker");
        RequestContextHolder.setRequestAttributes(new ServletRequestAttributes(request));
        BizConfig bizConfig = new BizConfig();
        bizConfig.setAdminUid("admin-owner");
        DataPermissionCheckTool tool = new DataPermissionCheckTool(
                mock(GroupVisibilityMapper.class),
                bizConfig,
                mock(RepoMapper.class),
                mock(SparkBotMapper.class),
                mock(WorkflowMapper.class),
                mock(DbInfoMapper.class),
                mock(DbTableMapper.class),
                mock(UserLangChainInfoMapper.class),
                mock(BotMarketDataService.class));
        Workflow workflow = new Workflow();
        workflow.setUid("admin-owner");
        workflow.setSpaceId(null);
        workflow.setIsPublic(Boolean.TRUE);

        assertThatThrownBy(() -> tool.checkWorkflowBelong(workflow, null))
                .isInstanceOf(BusinessException.class);
    }

    @Test
    void publicSpaceWorkflowRequiresMatchingMemberContextAcrossReadChecks() {
        BizConfig bizConfig = new BizConfig();
        bizConfig.setAdminUid("admin");
        DataPermissionCheckTool tool = new DataPermissionCheckTool(
                mock(GroupVisibilityMapper.class),
                bizConfig,
                mock(RepoMapper.class),
                mock(SparkBotMapper.class),
                mock(WorkflowMapper.class),
                mock(DbInfoMapper.class),
                mock(DbTableMapper.class),
                mock(UserLangChainInfoMapper.class),
                mock(BotMarketDataService.class));
        Workflow workflow = new Workflow();
        workflow.setUid("space-owner");
        workflow.setSpaceId(7L);
        workflow.setIsPublic(Boolean.TRUE);
        workflow.setExt("{}");

        try (MockedStatic<com.iflytek.astron.console.commons.util.space.SpaceInfoUtil> space =
                mockStatic(com.iflytek.astron.console.commons.util.space.SpaceInfoUtil.class)) {
            space.when(com.iflytek.astron.console.commons.util.space.SpaceInfoUtil::getSpaceId)
                    .thenReturn(null);
            space.when(com.iflytek.astron.console.commons.util.space.SpaceInfoUtil::checkUserBelongSpace)
                    .thenReturn(false);
            MockHttpServletRequest request = new MockHttpServletRequest();
            request.setAttribute(JwtClaimsFilter.USER_ID_ATTRIBUTE, "outsider");
            RequestContextHolder.setRequestAttributes(new ServletRequestAttributes(request));

            assertThatThrownBy(() -> tool.checkWorkflowVisible(workflow, null))
                    .isInstanceOf(BusinessException.class);
            assertThatThrownBy(() -> tool.checkWorkflowVisibleForDetail(workflow, null))
                    .isInstanceOf(BusinessException.class);

            space.when(com.iflytek.astron.console.commons.util.space.SpaceInfoUtil::getSpaceId)
                    .thenReturn(7L);
            assertThatThrownBy(() -> tool.checkWorkflowVisible(workflow, 7L))
                    .isInstanceOf(BusinessException.class);
            assertThatThrownBy(() -> tool.checkWorkflowVisibleForDetail(workflow, 7L))
                    .isInstanceOf(BusinessException.class);

            space.when(com.iflytek.astron.console.commons.util.space.SpaceInfoUtil::getSpaceId)
                    .thenReturn(8L);
            space.when(com.iflytek.astron.console.commons.util.space.SpaceInfoUtil::checkUserBelongSpace)
                    .thenReturn(true);
            assertThatThrownBy(() -> tool.checkWorkflowVisible(workflow, 7L))
                    .isInstanceOf(BusinessException.class);
            assertThatThrownBy(() -> tool.checkWorkflowVisibleForDetail(workflow, 7L))
                    .isInstanceOf(BusinessException.class);

            space.when(com.iflytek.astron.console.commons.util.space.SpaceInfoUtil::getSpaceId)
                    .thenReturn(7L);
            space.when(com.iflytek.astron.console.commons.util.space.SpaceInfoUtil::checkUserBelongSpace)
                    .thenReturn(true);
            tool.checkWorkflowVisible(workflow, 7L);
            tool.checkWorkflowVisibleForDetail(workflow, 7L);
        }
    }
}
