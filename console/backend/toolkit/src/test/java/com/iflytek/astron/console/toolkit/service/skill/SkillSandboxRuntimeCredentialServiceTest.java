package com.iflytek.astron.console.toolkit.service.skill;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.lenient;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import com.iflytek.astron.console.commons.constant.ResponseEnum;
import com.iflytek.astron.console.commons.entity.workflow.Workflow;
import com.iflytek.astron.console.commons.enums.space.SpaceRoleEnum;
import com.iflytek.astron.console.commons.exception.BusinessException;
import com.iflytek.astron.console.commons.service.space.SpaceUserService;
import com.iflytek.astron.console.toolkit.entity.dto.skill.SkillSandboxRuntimeCredentialDto;
import com.iflytek.astron.console.toolkit.entity.table.skill.SkillSandboxConfig;
import com.iflytek.astron.console.toolkit.mapper.skill.SkillSandboxConfigMapper;
import com.iflytek.astron.console.toolkit.mapper.workflow.WorkflowMapper;
import com.iflytek.astron.console.toolkit.security.SandboxRuntimeCredentialTokenProvider;
import java.util.List;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.test.util.ReflectionTestUtils;

@ExtendWith(MockitoExtension.class)
class SkillSandboxRuntimeCredentialServiceTest {

    private static final String SERVICE_TOKEN = "runtime-service-token";

    @Mock
    private SkillSandboxConfigMapper configMapper;

    @Mock
    private WorkflowMapper workflowMapper;

    @Mock
    private SpaceUserService spaceUserService;

    @Mock
    private SandboxRuntimeCredentialTokenProvider runtimeCredentialTokenProvider;

    private SkillSandboxConfigService service;

    @BeforeEach
    void setUp() {
        service = new SkillSandboxConfigService();
        ReflectionTestUtils.setField(service, "baseMapper", configMapper);
        ReflectionTestUtils.setField(service, "workflowMapper", workflowMapper);
        ReflectionTestUtils.setField(service, "spaceUserService", spaceUserService);
        ReflectionTestUtils.setField(
                service, "runtimeCredentialTokenProvider", runtimeCredentialTokenProvider);
        lenient().when(runtimeCredentialTokenProvider.matches(SERVICE_TOKEN)).thenReturn(true);
    }

    @Test
    void flowLookupDerivesTrustedScopeFromDatabase() {
        Workflow workflow = workflow("flow-1", "owner-1", null);
        when(workflowMapper.selectList(any())).thenReturn(List.of(workflow));
        when(configMapper.selectOne(any(), eq(false))).thenReturn(activeConfig("e2b-secret"));

        SkillSandboxRuntimeCredentialDto credential =
                service.getRuntimeCredential(SERVICE_TOKEN, " flow-1 ", "owner-1", null);

        assertThat(credential)
                .extracting(
                        SkillSandboxRuntimeCredentialDto::getProvider,
                        SkillSandboxRuntimeCredentialDto::getApiKey,
                        SkillSandboxRuntimeCredentialDto::getTimeoutSeconds,
                        SkillSandboxRuntimeCredentialDto::getAllowInternetAccess)
                .containsExactly("e2b", "e2b-secret", 60, false);
        verify(spaceUserService, never()).getRole(any(), any());
    }

    @Test
    void flowLookupRejectsMismatchedExecutionScope() {
        Workflow workflow = workflow("flow-1", "owner-1", null);
        when(workflowMapper.selectList(any())).thenReturn(List.of(workflow));

        assertThatThrownBy(
                () -> service.getRuntimeCredential(SERVICE_TOKEN, "flow-1", "attacker", null))
                .isInstanceOf(BusinessException.class)
                .extracting("responseEnum")
                .isEqualTo(ResponseEnum.INSUFFICIENT_PERMISSIONS);

        verify(configMapper, never()).selectOne(any(), eq(false));
    }

    @Test
    void ambiguousFlowIdFailsClosed() {
        when(workflowMapper.selectList(any()))
                .thenReturn(List.of(
                        workflow("flow-1", "owner-1", 100L),
                        workflow("flow-1", "owner-2", 200L)));

        assertThatThrownBy(
                        () -> service.getRuntimeCredential(
                                SERVICE_TOKEN, "flow-1", "owner-1", null))
                .isInstanceOf(BusinessException.class)
                .extracting("responseEnum")
                .isEqualTo(ResponseEnum.WORKFLOW_NOT_EXIST);

        verify(configMapper, never()).selectOne(any(), eq(false));
    }

    @Test
    void standaloneSpaceLookupRequiresCurrentMembership() {
        when(spaceUserService.getRole(100L, "member-1")).thenReturn(SpaceRoleEnum.MEMBER);
        when(configMapper.selectOne(any(), eq(false))).thenReturn(activeConfig("team-secret"));

        SkillSandboxRuntimeCredentialDto credential =
                service.getRuntimeCredential(SERVICE_TOKEN, null, "member-1", 100L);

        assertThat(credential.getApiKey()).isEqualTo("team-secret");
        verify(spaceUserService).getRole(100L, "member-1");
    }

    @Test
    void standaloneSpaceLookupRejectsFormerMemberBeforeSecretQuery() {
        when(spaceUserService.getRole(100L, "former-member")).thenReturn(null);

        assertThatThrownBy(
                        () -> service.getRuntimeCredential(
                                SERVICE_TOKEN, null, "former-member", 100L))
                .isInstanceOf(BusinessException.class)
                .extracting("responseEnum")
                .isEqualTo(ResponseEnum.INSUFFICIENT_PERMISSIONS);

        verify(configMapper, never()).selectOne(any(), eq(false));
    }

    @Test
    void rejectsMissingBrokerTokenBeforeAnySecretScopeLookup() {
        assertThatThrownBy(
                () -> service.getRuntimeCredential(
                        null, "flow-1", "owner-1", null))
                .isInstanceOf(BusinessException.class)
                .extracting("responseEnum")
                .isEqualTo(ResponseEnum.UNAUTHORIZED);

        verify(workflowMapper, never()).selectList(any());
        verify(configMapper, never()).selectOne(any(), eq(false));
    }

    private Workflow workflow(String flowId, String uid, Long spaceId) {
        Workflow workflow = new Workflow();
        workflow.setFlowId(flowId);
        workflow.setUid(uid);
        workflow.setSpaceId(spaceId);
        workflow.setDeleted(Boolean.FALSE);
        return workflow;
    }

    private SkillSandboxConfig activeConfig(String apiKey) {
        SkillSandboxConfig config = new SkillSandboxConfig();
        config.setProvider("e2b");
        config.setEnabled(Boolean.TRUE);
        config.setApiKey(apiKey);
        config.setDeleted(Boolean.FALSE);
        return config;
    }
}
