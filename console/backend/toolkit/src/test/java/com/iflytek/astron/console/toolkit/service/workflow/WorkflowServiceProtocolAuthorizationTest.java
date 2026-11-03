package com.iflytek.astron.console.toolkit.service.workflow;

import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import com.iflytek.astron.console.commons.constant.ResponseEnum;
import com.iflytek.astron.console.commons.config.JwtClaimsFilter;
import com.iflytek.astron.console.commons.entity.workflow.Workflow;
import com.iflytek.astron.console.commons.exception.BusinessException;
import com.iflytek.astron.console.commons.service.space.SpaceUserService;
import com.iflytek.astron.console.toolkit.entity.dto.WorkflowReq;
import com.iflytek.astron.console.toolkit.entity.dto.WorkflowComparisonReq;
import com.iflytek.astron.console.toolkit.entity.dto.eval.WorkflowComparisonSaveReq;
import com.iflytek.astron.console.toolkit.entity.table.workflow.WorkflowComparison;
import com.iflytek.astron.console.toolkit.entity.table.workflow.WorkflowDialog;
import com.iflytek.astron.console.toolkit.mapper.workflow.WorkflowComparisonMapper;
import com.iflytek.astron.console.toolkit.mapper.workflow.WorkflowDialogMapper;
import com.iflytek.astron.console.toolkit.mapper.workflow.WorkflowMapper;
import com.iflytek.astron.console.toolkit.mapper.workflow.WorkflowVersionMapper;
import com.iflytek.astron.console.toolkit.service.extra.CoreSystemService;
import com.iflytek.astron.console.toolkit.tool.DataPermissionCheckTool;
import java.util.List;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.BeforeAll;
import com.baomidou.mybatisplus.core.MybatisConfiguration;
import com.baomidou.mybatisplus.core.metadata.TableInfoHelper;
import org.apache.ibatis.builder.MapperBuilderAssistant;
import org.junit.jupiter.api.Test;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.test.util.ReflectionTestUtils;
import org.springframework.web.context.request.RequestContextHolder;
import org.springframework.web.context.request.ServletRequestAttributes;

class WorkflowServiceProtocolAuthorizationTest {

    @BeforeAll
    static void initTableInfo() {
        MapperBuilderAssistant assistant =
                new MapperBuilderAssistant(new MybatisConfiguration(), "");
        TableInfoHelper.initTableInfo(assistant, Workflow.class);
        TableInfoHelper.initTableInfo(assistant, WorkflowDialog.class);
    }

    private WorkflowService service;
    private WorkflowMapper workflowMapper;
    private WorkflowComparisonMapper comparisonMapper;
    private DataPermissionCheckTool permissionCheck;
    private CoreSystemService coreSystemService;
    private WorkflowDialogMapper workflowDialogMapper;
    private WorkflowVersionMapper workflowVersionMapper;
    private SpaceUserService spaceUserService;

    @BeforeEach
    void setUp() {
        service = new WorkflowService();
        workflowMapper = mock(WorkflowMapper.class);
        comparisonMapper = mock(WorkflowComparisonMapper.class);
        permissionCheck = mock(DataPermissionCheckTool.class);
        coreSystemService = mock(CoreSystemService.class);
        workflowDialogMapper = mock(WorkflowDialogMapper.class);
        workflowVersionMapper = mock(WorkflowVersionMapper.class);
        spaceUserService = mock(SpaceUserService.class);
        ReflectionTestUtils.setField(service, "baseMapper", workflowMapper);
        service.workflowMapper = workflowMapper;
        service.dataPermissionCheckTool = permissionCheck;
        service.coreSystemService = coreSystemService;
        ReflectionTestUtils.setField(service, "workflowComparisonMapper", comparisonMapper);
        ReflectionTestUtils.setField(service, "workflowDialogMapper", workflowDialogMapper);
        ReflectionTestUtils.setField(service, "workflowVersionMapper", workflowVersionMapper);
        ReflectionTestUtils.setField(service, "spaceUserService", spaceUserService);
        bindRequest("attacker", null);
    }

    @AfterEach
    void tearDown() {
        RequestContextHolder.resetRequestAttributes();
    }

    @Test
    void comparisonSaveRejectsMixedFlowIdsBeforeAnyDatabaseMutation() {
        WorkflowComparisonSaveReq first = comparison("flow-1", "prompt-1");
        WorkflowComparisonSaveReq second = comparison("flow-2", "prompt-1");

        assertThatThrownBy(() -> service.saveComparisons(List.of(first, second)))
                .isInstanceOf(BusinessException.class);

        verify(comparisonMapper, never()).delete(any());
        verify(comparisonMapper, never()).insert(any(WorkflowComparison.class));
    }

    @Test
    void comparisonListRejectsRowsSpanningMoreThanOneWorkflow() {
        WorkflowComparison first = storedComparison("flow-1", "prompt-1");
        WorkflowComparison second = storedComparison("flow-2", "prompt-1");
        when(comparisonMapper.selectList(any())).thenReturn(List.of(first, second));

        assertThatThrownBy(() -> service.listComparisons("prompt-1"))
                .isInstanceOf(BusinessException.class);

        verify(workflowMapper, never()).selectOne(any());
    }

    @Test
    void comparisonDeleteStopsBeforeCoreMutationWhenWorkflowIsUnauthorized() {
        Workflow victim = workflow("victim-flow");
        when(workflowMapper.selectOne(any())).thenReturn(victim);
        WorkflowComparisonReq request = new WorkflowComparisonReq();
        request.setFlowId("victim-flow");
        request.setVersion("v1");

        assertThatThrownBy(() -> service.deleteComparisons(request))
                .isInstanceOf(BusinessException.class);

        verify(coreSystemService, never()).deleteComparisons(any(), any());
    }

    @Test
    void copyFlowStopsBeforeUpdateWhenSourceIsNotVisible() {
        Workflow source = workflow("victim-flow");
        Workflow target = workflow("owned-flow");
        when(workflowMapper.selectOne(any())).thenReturn(source, target);
        doThrow(new BusinessException(ResponseEnum.INSUFFICIENT_PERMISSIONS))
                .when(permissionCheck)
                .checkWorkflowVisible(source, null);

        assertThatThrownBy(() -> service.copyFlow("victim-flow", "owned-flow"))
                .isInstanceOf(BusinessException.class);

        verify(workflowMapper, never()).updateById(any(Workflow.class));
    }

    @Test
    void publicVisibilityNeverAllowsComparisonOrCopyTargetMutation() {
        Workflow publicVictim = workflow("public-victim");
        publicVictim.setIsPublic(Boolean.TRUE);
        WorkflowComparisonSaveReq comparison = comparison("public-victim", "prompt-1");
        when(workflowMapper.selectOne(any())).thenReturn(publicVictim);

        assertThatThrownBy(() -> service.saveComparisons(List.of(comparison)))
                .isInstanceOf(BusinessException.class);

        verify(comparisonMapper, never()).delete(any());
        verify(comparisonMapper, never()).insert(any(WorkflowComparison.class));

        Workflow source = workflow("visible-source");
        Workflow publicTarget = workflow("public-target");
        publicTarget.setIsPublic(Boolean.TRUE);
        when(workflowMapper.selectOne(any())).thenReturn(source, publicTarget);

        assertThatThrownBy(() -> service.copyFlow("visible-source", "public-target"))
                .isInstanceOf(BusinessException.class);

        verify(workflowMapper, never()).updateById(any(Workflow.class));
    }

    @Test
    void replaceAppIdRejectsForeignPublicWorkflowBeforeUpdate() {
        Workflow publicVictim = workflow("public-victim");
        publicVictim.setIsPublic(Boolean.TRUE);
        when(workflowMapper.selectOne(any())).thenReturn(publicVictim);

        assertThatThrownBy(() -> service.replaceAppId("new-app", "public-victim"))
                .isInstanceOf(BusinessException.class);

        verify(workflowMapper, never()).updateById(any(Workflow.class));
    }

    @Test
    void replaceAppIdUsesLiteralReplacementAndRequiresOneUpdatedRow() {
        bindRequest("owner", null);
        Workflow owned = workflow("owned-flow");
        owned.setUid("owner");
        owned.setAppId("a.b");
        owned.setData("{\"exact\":\"a.b\",\"regexLike\":\"axb\"}");
        owned.setPublishedData("{\"exact\":\"a.b\"}");
        when(workflowMapper.selectOne(any())).thenReturn(owned);
        when(workflowMapper.updateById(owned)).thenReturn(1);

        service.replaceAppId("$1", "owned-flow");

        assertThat(owned.getData())
                .contains("$1", "axb")
                .doesNotContain("a.b");
        assertThat(owned.getPublishedData()).contains("$1");
        verify(workflowMapper).updateById(owned);

        when(workflowMapper.updateById(owned)).thenReturn(0);
        assertThatThrownBy(() -> service.replaceAppId("another", "owned-flow"))
                .isInstanceOf(BusinessException.class);
    }

    @Test
    void hasQaAndMaxVersionRejectBeforeInspectingUnauthorizedWorkflowData() {
        Workflow victim = workflow("victim-flow");
        victim.setData("{\"nodes\":[]}");
        when(workflowMapper.selectOne(any())).thenReturn(victim);
        doThrow(new BusinessException(ResponseEnum.INSUFFICIENT_PERMISSIONS))
                .when(permissionCheck)
                .checkWorkflowVisible(victim, null);
        com.iflytek.astron.console.commons.entity.bot.UserLangChainInfo binding =
                new com.iflytek.astron.console.commons.entity.bot.UserLangChainInfo();
        binding.setFlowId("victim-flow");
        com.iflytek.astron.console.commons.mapper.UserLangChainInfoMapper bindingMapper =
                mock(com.iflytek.astron.console.commons.mapper.UserLangChainInfoMapper.class);
        ReflectionTestUtils.setField(service, "userLangChainInfoDao", bindingMapper);
        when(bindingMapper.selectOne(any())).thenReturn(binding);

        assertThatThrownBy(() -> service.hasQaNode(7))
                .isInstanceOf(BusinessException.class);
        assertThatThrownBy(() -> service.getMaxVersionByFlowId("victim-flow"))
                .isInstanceOf(BusinessException.class)
                .extracting("responseEnum")
                .isEqualTo(ResponseEnum.INSUFFICIENT_PERMISSIONS);

        verify(workflowVersionMapper, never()).selectOne(any());
    }

    @Test
    void updateAndBuildRejectForeignPublicWorkflowAndForgedSpaceBeforeMutation()
            throws Exception {
        Workflow publicVictim = workflow("public-victim");
        publicVictim.setIsPublic(Boolean.TRUE);
        when(workflowMapper.selectById(publicVictim.getId())).thenReturn(publicVictim);
        WorkflowReq update = new WorkflowReq();
        update.setId(publicVictim.getId());

        assertThatThrownBy(() -> service.updateInfo(update))
                .isInstanceOf(BusinessException.class);
        assertThatThrownBy(() -> service.build(update))
                .isInstanceOf(BusinessException.class);

        WorkflowReq forgedSpace = new WorkflowReq();
        forgedSpace.setId(publicVictim.getId());
        forgedSpace.setSpaceId(999L);
        assertThatThrownBy(() -> service.updateInfo(forgedSpace))
                .isInstanceOf(BusinessException.class);

        verify(workflowMapper, never()).updateById(any(Workflow.class));
    }

    @Test
    void clearDialogAuthorizesWorkflowAndScopesDeletionToCurrentUid() {
        bindRequest("owner", null);
        Workflow owned = workflow("owned-flow");
        owned.setUid("owner");
        when(workflowMapper.selectOne(any())).thenReturn(owned);
        when(workflowDialogMapper.update(any())).thenReturn(2);

        service.clearDialog(owned.getId(), 1);

        @SuppressWarnings("unchecked")
        org.mockito.ArgumentCaptor<com.baomidou.mybatisplus.core.conditions.update.LambdaUpdateWrapper<com.iflytek.astron.console.toolkit.entity.table.workflow.WorkflowDialog>> update = org.mockito.ArgumentCaptor.forClass(
                com.baomidou.mybatisplus.core.conditions.update.LambdaUpdateWrapper.class);
        verify(workflowDialogMapper).update(update.capture());
        assertThat(update.getValue().getSqlSegment().toLowerCase())
                .contains("workflow_id", "uid", "type");
        assertThat(update.getValue().getParamNameValuePairs())
                .containsValue("owner")
                .containsValue(owned.getId());

        bindRequest("attacker", null);
        assertThatThrownBy(() -> service.clearDialog(owned.getId(), 1))
                .isInstanceOf(BusinessException.class);
    }

    private WorkflowComparisonSaveReq comparison(String flowId, String promptId) {
        WorkflowComparisonSaveReq request = new WorkflowComparisonSaveReq();
        request.setFlowId(flowId);
        request.setPromptId(promptId);
        return request;
    }

    private WorkflowComparison storedComparison(String flowId, String promptId) {
        WorkflowComparison comparison = new WorkflowComparison();
        comparison.setFlowId(flowId);
        comparison.setPromptId(promptId);
        comparison.setData("{}");
        return comparison;
    }

    private Workflow workflow(String flowId) {
        Workflow workflow = new Workflow();
        workflow.setId(42L);
        workflow.setFlowId(flowId);
        workflow.setUid("owner");
        workflow.setDeleted(false);
        workflow.setData("{}");
        return workflow;
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
