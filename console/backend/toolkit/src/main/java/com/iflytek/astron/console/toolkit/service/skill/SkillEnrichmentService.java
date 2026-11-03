package com.iflytek.astron.console.toolkit.service.skill;

import com.alibaba.fastjson2.JSON;
import com.alibaba.fastjson2.JSONArray;
import com.iflytek.astron.console.toolkit.entity.dto.skill.SkillImportDto;
import com.iflytek.astron.console.toolkit.entity.dto.skill.SkillImportResourceDto;
import com.iflytek.astron.console.toolkit.entity.dto.skill.SkillSandboxRuntimeRefDto;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Set;
import java.util.stream.Collectors;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.apache.commons.lang3.StringUtils;
import org.springframework.stereotype.Service;

/**
 * Shared skill enrichment: augments skill entries (each carrying a skillId) in place with
 * name/description/downloadUrl/resources and, when the sandbox is enabled, sandbox config. Used by
 * both the workflow agent node and the standalone agent runtime.
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class SkillEnrichmentService {

    private static final Set<String> SERVER_DERIVED_FIELDS = Set.of(
            "sandbox",
            "name",
            "description",
            "downloadUrl",
            "download_url",
            "resources");
    private static final int MAX_RUNTIME_RESOURCES = 100;
    private static final long MAX_RUNTIME_RESOURCE_BYTES = 5L * 1024 * 1024;
    private static final long MAX_RUNTIME_TOTAL_RESOURCE_BYTES = 20L * 1024 * 1024;

    private final SkillFileService skillFileService;
    private final SkillSandboxConfigService skillSandboxConfigService;

    public void enrichSkillEntries(JSONArray skillArray) {
        enrichSkillEntries(skillArray, null, null);
    }

    /**
     * Enriches skills for the supplied execution identity. When {@code uid} is absent the legacy
     * request-scoped behavior is preserved for synchronous callers.
     */
    public void enrichSkillEntries(JSONArray skillArray, String uid, Long spaceId) {
        if (skillArray == null || skillArray.isEmpty()) {
            return;
        }
        Set<Long> skillIds = new LinkedHashSet<>();
        for (int i = skillArray.size() - 1; i >= 0; i--) {
            Object obj = skillArray.get(i);
            if (!(obj instanceof Map skillObj)) {
                skillArray.remove(i);
                continue;
            }
            // Runtime metadata is server-derived. Remove every historical/client value before
            // looking at the id so no invalid or unauthorized entry can retain an attacker URL.
            SERVER_DERIVED_FIELDS.forEach(skillObj::remove);
            Object skillIdObj = skillIdentifier(skillObj);
            try {
                long skillId = Long.parseLong(String.valueOf(skillIdObj));
                if (skillId <= 0) {
                    throw new NumberFormatException("non-positive skill id");
                }
                skillIds.add(skillId);
            } catch (NumberFormatException ex) {
                log.warn("Ignore invalid skill id: {}", skillIdObj);
                skillArray.remove(i);
            }
        }
        if (skillIds.isEmpty()) {
            return;
        }
        List<Long> requestedIds = skillIds.stream().sorted().toList();
        List<SkillImportDto> imports = uid == null
                ? skillFileService.getSkillImportsByIds(requestedIds)
                : skillFileService.getSkillImportsByIds(requestedIds, uid, spaceId);
        Map<Long, SkillImportDto> importMap = Objects.requireNonNullElse(imports, List.<SkillImportDto>of())
                .stream()
                .filter(Objects::nonNull)
                .filter(item -> item.getId() != null)
                .collect(Collectors.toMap(SkillImportDto::getId, item -> item, (a, b) -> a));
        if (importMap.isEmpty()) {
            skillArray.clear();
            return;
        }
        SkillSandboxRuntimeRefDto sandboxConfig = uid == null
                ? skillSandboxConfigService.toRuntimeRefDto()
                : skillSandboxConfigService.toRuntimeRefDto(uid, spaceId);
        for (int i = skillArray.size() - 1; i >= 0; i--) {
            Object obj = skillArray.get(i);
            if (!(obj instanceof Map skillObj)) {
                skillArray.remove(i);
                continue;
            }
            Object skillIdObj = skillIdentifier(skillObj);
            try {
                Long skillId = Long.parseLong(String.valueOf(skillIdObj));
                SkillImportDto importDto = importMap.get(skillId);
                if (importDto == null) {
                    // A missing import also covers an id outside the caller's authoritative scope.
                    skillArray.remove(i);
                    continue;
                }
                skillObj.remove("id");
                skillObj.put("skillId", String.valueOf(importDto.getId()));
                skillObj.put("name", StringUtils.defaultString(importDto.getName()));
                skillObj.put("description", StringUtils.defaultString(importDto.getDescription()));
                skillObj.put("downloadUrl", StringUtils.defaultString(importDto.getDownloadUrl()));
                skillObj.put("resources", runtimeResources(importDto.getResources()));
                if (sandboxConfig != null && Boolean.TRUE.equals(sandboxConfig.getEnabled())) {
                    skillObj.put("sandbox", JSON.parseObject(JSON.toJSONString(sandboxConfig)));
                }
            } catch (NumberFormatException ex) {
                log.warn("Ignore invalid skill id while enriching: {}", skillIdObj);
                skillArray.remove(i);
            }
        }
    }

    private Object skillIdentifier(Map<?, ?> skillObj) {
        Object skillId = skillObj.get("skillId");
        return skillId == null ? skillObj.get("id") : skillId;
    }

    private List<SkillImportResourceDto> runtimeResources(
            List<SkillImportResourceDto> resources) {
        if (resources == null || resources.isEmpty()) {
            return List.of();
        }
        List<SkillImportResourceDto> accepted = new ArrayList<>();
        long totalBytes = 0;
        for (SkillImportResourceDto resource : resources) {
            if (accepted.size() >= MAX_RUNTIME_RESOURCES) {
                break;
            }
            if (resource == null) {
                continue;
            }
            String path = StringUtils.trimToEmpty(resource.getPath()).replace('\\', '/');
            Long declaredSize = resource.getFileSize();
            long size = declaredSize == null ? 0 : declaredSize;
            if (StringUtils.isAnyBlank(path, resource.getDownloadUrl())
                    || path.startsWith("/")
                    || path.length() > 1024
                    || List.of(path.split("/", -1))
                            .stream()
                            .anyMatch(segment -> StringUtils.equalsAny(segment, "", ".", ".."))
                    || size < 0
                    || size > MAX_RUNTIME_RESOURCE_BYTES
                    || totalBytes > MAX_RUNTIME_TOTAL_RESOURCE_BYTES - size) {
                continue;
            }
            accepted.add(resource);
            totalBytes += size;
        }
        return accepted;
    }
}
