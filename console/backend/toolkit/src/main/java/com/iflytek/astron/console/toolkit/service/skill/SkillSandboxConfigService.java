package com.iflytek.astron.console.toolkit.service.skill;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.core.toolkit.Wrappers;
import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import com.iflytek.astron.console.commons.constant.ResponseEnum;
import com.iflytek.astron.console.commons.entity.workflow.Workflow;
import com.iflytek.astron.console.commons.exception.BusinessException;
import com.iflytek.astron.console.commons.service.space.SpaceUserService;
import com.iflytek.astron.console.commons.util.space.SpaceInfoUtil;
import com.iflytek.astron.console.toolkit.entity.dto.skill.SkillSandboxConfigDto;
import com.iflytek.astron.console.toolkit.entity.dto.skill.SkillSandboxRuntimeCredentialDto;
import com.iflytek.astron.console.toolkit.entity.dto.skill.SkillSandboxRuntimeRefDto;
import com.iflytek.astron.console.toolkit.entity.table.skill.SkillSandboxConfig;
import com.iflytek.astron.console.toolkit.entity.vo.skill.SkillSandboxConfigReq;
import com.iflytek.astron.console.toolkit.handler.UserInfoManagerHandler;
import com.iflytek.astron.console.toolkit.mapper.skill.SkillSandboxConfigMapper;
import com.iflytek.astron.console.toolkit.mapper.workflow.WorkflowMapper;
import com.iflytek.astron.console.toolkit.security.SandboxRuntimeCredentialTokenProvider;
import jakarta.annotation.Resource;
import java.time.LocalDateTime;
import java.util.List;
import lombok.extern.slf4j.Slf4j;
import org.apache.commons.lang3.StringUtils;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
@Slf4j
public class SkillSandboxConfigService
        extends ServiceImpl<SkillSandboxConfigMapper, SkillSandboxConfig> {

    private static final String PROVIDER_E2B = "e2b";
    private static final int DEFAULT_TIMEOUT_SECONDS = 60;

    @Resource
    private SpaceUserService spaceUserService;

    @Resource
    private WorkflowMapper workflowMapper;

    @Resource
    private SandboxRuntimeCredentialTokenProvider runtimeCredentialTokenProvider;

    public SkillSandboxConfigDto getMaskedConfig() {
        return toDto(getScopedConfig(), false);
    }

    public SkillSandboxConfig getActiveConfig() {
        SkillSandboxConfig config = getScopedConfig();
        if (config == null
                || !Boolean.TRUE.equals(config.getEnabled())
                || StringUtils.isBlank(config.getApiKey())) {
            return null;
        }
        return config;
    }

    @Transactional
    public SkillSandboxConfigDto saveConfig(SkillSandboxConfigReq req) {
        SkillSandboxConfig existing = getScopedConfig();
        LocalDateTime now = LocalDateTime.now();
        SkillSandboxConfig config = existing == null ? new SkillSandboxConfig() : existing;
        if (existing == null) {
            config.setUid(currentUid());
            config.setSpaceId(currentSpaceId());
            config.setDeleted(Boolean.FALSE);
            config.setCreateTime(now);
        }
        config.setProvider(normalizeProvider(req == null ? null : req.getProvider()));
        config.setEnabled(req != null && Boolean.TRUE.equals(req.getEnabled()));
        config.setTimeoutSeconds(normalizeTimeout(req == null ? null : req.getTimeoutSeconds()));
        config.setAllowInternetAccess(req != null && Boolean.TRUE.equals(req.getAllowInternetAccess()));
        if (req != null && !Boolean.TRUE.equals(req.getApiKeyMasked())) {
            config.setApiKey(StringUtils.trimToEmpty(req.getApiKey()));
        }
        config.setUpdateTime(now);
        if (config.getId() == null) {
            save(config);
        } else {
            updateById(config);
        }
        return toDto(config, false);
    }

    @Transactional
    public SkillSandboxConfigDto testConfig(SkillSandboxConfigReq req) {
        SkillSandboxConfigDto dto = saveConfig(req);
        SkillSandboxConfig config = getScopedConfig();
        LocalDateTime now = LocalDateTime.now();
        if (config == null || StringUtils.isBlank(config.getApiKey())) {
            dto.setLastTestStatus("failed");
            dto.setLastTestMessage("E2B API Key is empty");
        } else {
            dto.setLastTestStatus("success");
            dto.setLastTestMessage("Sandbox configuration is saved. Live E2B execution is verified when a Skill script runs.");
        }
        dto.setLastTestTime(now);
        if (config != null) {
            config.setLastTestStatus(dto.getLastTestStatus());
            config.setLastTestMessage(dto.getLastTestMessage());
            config.setLastTestTime(now);
            config.setUpdateTime(now);
            updateById(config);
        }
        return dto;
    }

    public SkillSandboxRuntimeRefDto toRuntimeRefDto() {
        return toRuntimeRefDto(currentUid(), currentSpaceId());
    }

    /** Resolve a non-secret runtime reference without relying on servlet request context. */
    public SkillSandboxRuntimeRefDto toRuntimeRefDto(String uid, Long spaceId) {
        SkillSandboxConfig config = getActiveConfig(uid, spaceId);
        SkillSandboxRuntimeRefDto dto = new SkillSandboxRuntimeRefDto();
        dto.setProvider(PROVIDER_E2B);
        dto.setEnabled(config != null);
        dto.setUid(uid);
        dto.setSpaceId(spaceId);
        return dto;
    }

    /**
     * Resolve the E2B credential only for the authenticated private broker. A workflow reference
     * derives scope from the database; standalone agent calls must provide a currently authorized
     * uid/space pair.
     */
    public SkillSandboxRuntimeCredentialDto getRuntimeCredential(
            String serviceToken, String flowId, String uid, Long spaceId) {
        if (runtimeCredentialTokenProvider == null
                || !runtimeCredentialTokenProvider.matches(serviceToken)) {
            throw new BusinessException(ResponseEnum.UNAUTHORIZED);
        }
        assertExplicitScope(uid, spaceId);
        SkillSandboxConfig config;
        if (StringUtils.isNotBlank(flowId)) {
            List<Workflow> workflows = workflowMapper.selectList(
                    Wrappers.lambdaQuery(Workflow.class)
                            .eq(Workflow::getFlowId, StringUtils.trim(flowId))
                            .eq(Workflow::getDeleted, Boolean.FALSE)
                            .last("limit 2"));
            if (workflows == null || workflows.size() != 1) {
                throw new BusinessException(ResponseEnum.WORKFLOW_NOT_EXIST);
            }
            Workflow workflow = workflows.getFirst();
            assertWorkflowExecutionScope(workflow, uid, spaceId);
            config = getActiveConfigForTrustedScope(workflow.getUid(), workflow.getSpaceId());
        } else {
            config = getActiveConfigForTrustedScope(uid, spaceId);
        }
        if (config == null) {
            throw new BusinessException(ResponseEnum.DATA_NOT_EXIST);
        }
        return new SkillSandboxRuntimeCredentialDto(
                normalizeProvider(config.getProvider()),
                config.getApiKey(),
                normalizeTimeout(config.getTimeoutSeconds()),
                Boolean.TRUE.equals(config.getAllowInternetAccess()));
    }

    private void assertWorkflowExecutionScope(Workflow workflow, String uid, Long spaceId) {
        if (workflow.getSpaceId() == null) {
            if (spaceId != null || !StringUtils.equals(workflow.getUid(), uid)) {
                throw new BusinessException(ResponseEnum.INSUFFICIENT_PERMISSIONS);
            }
            return;
        }
        if (!java.util.Objects.equals(workflow.getSpaceId(), spaceId)) {
            throw new BusinessException(ResponseEnum.INSUFFICIENT_PERMISSIONS);
        }
    }

    private SkillSandboxConfig getActiveConfig(String uid, Long spaceId) {
        SkillSandboxConfig config = getScopedConfig(uid, spaceId);
        if (config == null
                || !Boolean.TRUE.equals(config.getEnabled())
                || StringUtils.isBlank(config.getApiKey())) {
            return null;
        }
        return config;
    }

    private SkillSandboxConfig getActiveConfigForTrustedScope(String uid, Long spaceId) {
        if (spaceId == null && StringUtils.isBlank(uid)) {
            throw new BusinessException(ResponseEnum.UNAUTHORIZED);
        }
        SkillSandboxConfig config = getOne(scopeQuery(uid, spaceId), false);
        if (config == null
                || !Boolean.TRUE.equals(config.getEnabled())
                || StringUtils.isBlank(config.getApiKey())) {
            return null;
        }
        return config;
    }

    private SkillSandboxConfig getScopedConfig() {
        return getOne(scopeQuery(), false);
    }

    private SkillSandboxConfig getScopedConfig(String uid, Long spaceId) {
        assertExplicitScope(uid, spaceId);
        return getOne(scopeQuery(uid, spaceId), false);
    }

    private void assertExplicitScope(String uid, Long spaceId) {
        if (StringUtils.isBlank(uid)) {
            throw new BusinessException(ResponseEnum.UNAUTHORIZED);
        }
        if (spaceId != null
                && (spaceUserService == null
                        || spaceUserService.getRole(spaceId, uid) == null)) {
            throw new BusinessException(ResponseEnum.INSUFFICIENT_PERMISSIONS);
        }
    }

    private LambdaQueryWrapper<SkillSandboxConfig> scopeQuery() {
        return scopeQuery(currentUid(), currentSpaceId());
    }

    private LambdaQueryWrapper<SkillSandboxConfig> scopeQuery(String uid, Long spaceId) {
        LambdaQueryWrapper<SkillSandboxConfig> wrapper = Wrappers.lambdaQuery(SkillSandboxConfig.class)
                .eq(SkillSandboxConfig::getDeleted, Boolean.FALSE);
        if (spaceId != null) {
            wrapper.eq(SkillSandboxConfig::getSpaceId, spaceId);
        } else {
            if (StringUtils.isBlank(uid)) {
                throw new BusinessException(ResponseEnum.UNAUTHORIZED);
            }
            wrapper.isNull(SkillSandboxConfig::getSpaceId).eq(SkillSandboxConfig::getUid, uid);
        }
        return wrapper.last("limit 1");
    }

    private SkillSandboxConfigDto toDto(SkillSandboxConfig config, boolean includeSecret) {
        SkillSandboxConfigDto dto = new SkillSandboxConfigDto();
        dto.setProvider(PROVIDER_E2B);
        dto.setEnabled(Boolean.FALSE);
        dto.setTimeoutSeconds(DEFAULT_TIMEOUT_SECONDS);
        dto.setAllowInternetAccess(Boolean.FALSE);
        dto.setApiKey("");
        dto.setApiKeyMasked(Boolean.FALSE);
        if (config == null) {
            return dto;
        }
        dto.setProvider(normalizeProvider(config.getProvider()));
        dto.setEnabled(Boolean.TRUE.equals(config.getEnabled()));
        dto.setTimeoutSeconds(normalizeTimeout(config.getTimeoutSeconds()));
        dto.setAllowInternetAccess(Boolean.TRUE.equals(config.getAllowInternetAccess()));
        dto.setApiKey(includeSecret ? StringUtils.defaultString(config.getApiKey()) : maskApiKey(config.getApiKey()));
        dto.setApiKeyMasked(StringUtils.isNotBlank(config.getApiKey()) && !includeSecret);
        dto.setLastTestStatus(config.getLastTestStatus());
        dto.setLastTestMessage(config.getLastTestMessage());
        dto.setLastTestTime(config.getLastTestTime());
        return dto;
    }

    private String maskApiKey(String apiKey) {
        if (StringUtils.isBlank(apiKey)) {
            return "";
        }
        if (apiKey.length() <= 8) {
            return "********";
        }
        return apiKey.substring(0, 4) + "********" + apiKey.substring(apiKey.length() - 4);
    }

    private String normalizeProvider(String provider) {
        return PROVIDER_E2B;
    }

    private int normalizeTimeout(Integer timeoutSeconds) {
        if (timeoutSeconds == null || timeoutSeconds < 1) {
            return DEFAULT_TIMEOUT_SECONDS;
        }
        return Math.min(timeoutSeconds, 600);
    }

    private String currentUid() {
        return UserInfoManagerHandler.getUserId();
    }

    private Long currentSpaceId() {
        return SpaceInfoUtil.getSpaceId();
    }
}
