package com.iflytek.astron.console.toolkit.service.skill;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

import com.iflytek.astron.console.commons.constant.ResponseEnum;
import com.iflytek.astron.console.commons.enums.space.SpaceRoleEnum;
import com.iflytek.astron.console.commons.exception.BusinessException;
import com.iflytek.astron.console.commons.service.space.SpaceUserService;
import com.iflytek.astron.console.toolkit.entity.dto.skill.SkillSandboxRuntimeRefDto;
import com.iflytek.astron.console.toolkit.entity.table.skill.SkillSandboxConfig;
import com.iflytek.astron.console.toolkit.mapper.skill.SkillSandboxConfigMapper;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.test.util.ReflectionTestUtils;
import org.springframework.web.context.request.RequestContextHolder;

@ExtendWith(MockitoExtension.class)
class SkillSandboxConfigServiceExplicitScopeTest {

    @Mock
    private SkillSandboxConfigMapper mapper;

    @Mock
    private SpaceUserService spaceUserService;

    private SkillSandboxConfigService service;

    @BeforeEach
    void setUp() {
        service = new SkillSandboxConfigService();
        ReflectionTestUtils.setField(service, "baseMapper", mapper);
        ReflectionTestUtils.setField(service, "spaceUserService", spaceUserService);
    }

    @Test
    void resolvesExplicitTeamScopeForCurrentMemberWithoutRequestContext() {
        RequestContextHolder.resetRequestAttributes();
        SkillSandboxConfig config = new SkillSandboxConfig();
        config.setUid("space-owner");
        config.setSpaceId(100L);
        config.setEnabled(Boolean.TRUE);
        config.setApiKey("team-secret");
        config.setDeleted(Boolean.FALSE);
        when(spaceUserService.getRole(100L, "current-member"))
                .thenReturn(SpaceRoleEnum.MEMBER);
        when(mapper.selectOne(any(), eq(false))).thenReturn(config);

        SkillSandboxRuntimeRefDto dto = service.toRuntimeRefDto("current-member", 100L);

        assertThat(dto.getEnabled()).isTrue();
        assertThat(dto.getUid()).isEqualTo("current-member");
        assertThat(dto.getSpaceId()).isEqualTo(100L);
        verify(spaceUserService).getRole(100L, "current-member");
    }

    @Test
    void rejectsExplicitTeamScopeForFormerMemberBeforeQuery() {
        RequestContextHolder.resetRequestAttributes();
        when(spaceUserService.getRole(100L, "former-member")).thenReturn(null);

        assertThatThrownBy(() -> service.toRuntimeRefDto("former-member", 100L))
                .isInstanceOf(BusinessException.class)
                .extracting("responseEnum")
                .isEqualTo(ResponseEnum.INSUFFICIENT_PERMISSIONS);

        verify(spaceUserService).getRole(100L, "former-member");
        verifyNoInteractions(mapper);
    }

    @Test
    void resolvesExplicitPersonalScopeWithoutRequestContext() {
        RequestContextHolder.resetRequestAttributes();
        SkillSandboxConfig config = new SkillSandboxConfig();
        config.setUid("approval-user");
        config.setEnabled(Boolean.TRUE);
        config.setApiKey("approval-secret");
        config.setTimeoutSeconds(90);
        config.setDeleted(Boolean.FALSE);
        when(mapper.selectOne(any(), eq(false))).thenReturn(config);

        SkillSandboxRuntimeRefDto dto = service.toRuntimeRefDto("approval-user", null);

        assertThat(dto.getEnabled()).isTrue();
        assertThat(dto.getUid()).isEqualTo("approval-user");
        assertThat(dto.getSpaceId()).isNull();
    }

    @Test
    void rejectsBlankExplicitPersonalUidWithoutRequestContext() {
        RequestContextHolder.resetRequestAttributes();

        assertThatThrownBy(() -> service.toRuntimeRefDto(" ", null))
                .isInstanceOf(BusinessException.class)
                .extracting("responseEnum")
                .isEqualTo(ResponseEnum.UNAUTHORIZED);
    }
}
