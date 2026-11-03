package com.iflytek.astron.console.toolkit.service.workflow;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.inOrder;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;
import static org.mockito.ArgumentMatchers.isNull;

import com.iflytek.astron.console.commons.config.JwtClaimsFilter;
import com.iflytek.astron.console.commons.entity.workflow.Workflow;
import com.iflytek.astron.console.commons.exception.BusinessException;
import com.iflytek.astron.console.commons.service.space.SpaceUserService;
import com.iflytek.astron.console.toolkit.mapper.relation.FlowRepoRelMapper;
import com.iflytek.astron.console.toolkit.mapper.relation.FlowToolRelMapper;
import com.iflytek.astron.console.toolkit.mapper.workflow.WorkflowMapper;
import com.iflytek.astron.console.toolkit.tool.DataPermissionCheckTool;
import com.baomidou.mybatisplus.core.MybatisConfiguration;
import com.baomidou.mybatisplus.core.metadata.TableInfoHelper;
import org.apache.ibatis.builder.MapperBuilderAssistant;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.InOrder;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.test.util.ReflectionTestUtils;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.web.context.request.RequestContextHolder;
import org.springframework.web.context.request.ServletRequestAttributes;

@ExtendWith(MockitoExtension.class)
class WorkflowServiceArtifactDeletionTest {

    @BeforeAll
    static void initTableInfo() {
        TableInfoHelper.initTableInfo(
                new MapperBuilderAssistant(new MybatisConfiguration(), ""), Workflow.class);
    }

    @Mock
    private WorkflowMapper workflowMapper;

    @Mock
    private WorkflowArtifactService workflowArtifactService;

    @Mock
    private DataPermissionCheckTool dataPermissionCheckTool;

    @Mock
    private FlowToolRelMapper flowToolRelMapper;

    @Mock
    private FlowRepoRelMapper flowRepoRelMapper;

    @Mock
    private SpaceUserService spaceUserService;

    private WorkflowService workflowService;

    @BeforeEach
    void setUp() {
        workflowService = new WorkflowService();
        ReflectionTestUtils.setField(workflowService, "baseMapper", workflowMapper);
        ReflectionTestUtils.setField(workflowService, "workflowMapper", workflowMapper);
        ReflectionTestUtils.setField(
                workflowService, "workflowArtifactService", workflowArtifactService);
        ReflectionTestUtils.setField(
                workflowService, "dataPermissionCheckTool", dataPermissionCheckTool);
        ReflectionTestUtils.setField(workflowService, "flowToolRelMapper", flowToolRelMapper);
        ReflectionTestUtils.setField(workflowService, "flowRepoRelMapper", flowRepoRelMapper);
        ReflectionTestUtils.setField(workflowService, "spaceUserService", spaceUserService);
        bindRequest("owner", null);
    }

    @AfterEach
    void tearDown() {
        RequestContextHolder.resetRequestAttributes();
    }

    @Test
    void logicDeleteTombstonesArtifactsAfterMarkingWorkflowDeleted() {
        Workflow workflow = new Workflow();
        workflow.setId(42L);
        workflow.setUid("owner");
        workflow.setFlowId(null);
        workflow.setDeleted(Boolean.FALSE);
        when(workflowMapper.selectOne(any())).thenReturn(workflow);
        when(workflowMapper.update(isNull(), any())).thenReturn(1);

        workflowService.logicDelete(42L, null);

        assertThat(workflow.getDeleted()).isTrue();
        InOrder deletionOrder = inOrder(workflowMapper, workflowArtifactService);
        deletionOrder.verify(workflowMapper).update(isNull(), any());
        deletionOrder.verify(workflowArtifactService).tombstoneWorkflowArtifacts(42L);
    }

    @Test
    void logicDeleteDoesNotTombstoneArtifactsWhenWorkflowUpdateFails() {
        Workflow workflow = new Workflow();
        workflow.setId(42L);
        workflow.setUid("owner");
        workflow.setDeleted(Boolean.FALSE);
        when(workflowMapper.selectOne(any())).thenReturn(workflow);
        when(workflowMapper.update(isNull(), any())).thenReturn(0);

        assertThatThrownBy(() -> workflowService.logicDelete(42L, null))
                .isInstanceOf(BusinessException.class);

        verify(workflowArtifactService, never()).tombstoneWorkflowArtifacts(any());
    }

    @Test
    void logicDeleteRejectsForgedSpacePublicAttackerAndNonMember() {
        Workflow publicVictim = new Workflow();
        publicVictim.setId(42L);
        publicVictim.setUid("victim");
        publicVictim.setDeleted(Boolean.FALSE);
        publicVictim.setIsPublic(Boolean.TRUE);
        when(workflowMapper.selectOne(any())).thenReturn(publicVictim);

        assertThatThrownBy(() -> workflowService.logicDelete(42L, null))
                .isInstanceOf(BusinessException.class);
        assertThatThrownBy(() -> workflowService.logicDelete(42L, 999L))
                .isInstanceOf(BusinessException.class);

        bindRequest("attacker", 100L);
        Workflow spaceVictim = new Workflow();
        spaceVictim.setId(42L);
        spaceVictim.setUid("owner");
        spaceVictim.setSpaceId(100L);
        spaceVictim.setDeleted(Boolean.FALSE);
        when(workflowMapper.selectOne(any())).thenReturn(spaceVictim);
        when(spaceUserService.getRole(100L, "attacker")).thenReturn(null);

        assertThatThrownBy(() -> workflowService.logicDelete(42L, 100L))
                .isInstanceOf(BusinessException.class);

        verify(workflowMapper, never()).update(isNull(), any());
        verify(workflowArtifactService, never()).tombstoneWorkflowArtifacts(any());
    }

    private void bindRequest(String uid, Long spaceId) {
        MockHttpServletRequest request = new MockHttpServletRequest();
        request.setAttribute(JwtClaimsFilter.USER_ID_ATTRIBUTE, uid);
        if (spaceId != null) {
            request.addHeader("space-id", String.valueOf(spaceId));
        }
        RequestContextHolder.setRequestAttributes(new ServletRequestAttributes(request));
    }
}
