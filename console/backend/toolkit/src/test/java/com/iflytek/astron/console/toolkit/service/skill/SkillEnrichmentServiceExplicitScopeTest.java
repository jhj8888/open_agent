package com.iflytek.astron.console.toolkit.service.skill;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

import com.alibaba.fastjson2.JSONArray;
import com.alibaba.fastjson2.JSONObject;
import com.iflytek.astron.console.toolkit.entity.dto.skill.SkillImportDto;
import com.iflytek.astron.console.toolkit.entity.dto.skill.SkillImportResourceDto;
import com.iflytek.astron.console.toolkit.entity.dto.skill.SkillSandboxRuntimeRefDto;
import java.util.List;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.web.context.request.RequestContextHolder;

@ExtendWith(MockitoExtension.class)
class SkillEnrichmentServiceExplicitScopeTest {

    @Mock
    private SkillFileService skillFileService;

    @Mock
    private SkillSandboxConfigService skillSandboxConfigService;

    @InjectMocks
    private SkillEnrichmentService skillEnrichmentService;

    @Test
    void enrichesDownloadResourcesAndSandboxWithoutRequestContext() {
        RequestContextHolder.resetRequestAttributes();
        SkillImportResourceDto resource = new SkillImportResourceDto();
        resource.setPath("scripts/run.py");
        resource.setDownloadUrl("https://example.test/resource");
        SkillImportDto skill = new SkillImportDto();
        skill.setId(42L);
        skill.setName("approval-skill");
        skill.setDescription("approval description");
        skill.setDownloadUrl("https://example.test/skill");
        skill.setResources(List.of(resource));
        when(skillFileService.getSkillImportsByIds(List.of(42L), "approval-user", 200L))
                .thenReturn(List.of(skill));

        SkillSandboxRuntimeRefDto sandbox = new SkillSandboxRuntimeRefDto();
        sandbox.setEnabled(Boolean.TRUE);
        sandbox.setUid("approval-user");
        sandbox.setSpaceId(200L);
        when(skillSandboxConfigService.toRuntimeRefDto("approval-user", 200L)).thenReturn(sandbox);

        JSONArray entries = new JSONArray(List.of(new JSONObject().fluentPut("skillId", "42")));
        skillEnrichmentService.enrichSkillEntries(entries, "approval-user", 200L);

        JSONObject enriched = entries.getJSONObject(0);
        assertThat(enriched.getString("name")).isEqualTo("approval-skill");
        assertThat(enriched.getString("downloadUrl")).isEqualTo("https://example.test/skill");
        assertThat(enriched.getJSONArray("resources").getJSONObject(0).getString("path"))
                .isEqualTo("scripts/run.py");
        assertThat(enriched.getJSONObject("sandbox"))
                .doesNotContainKey("apiKey")
                .doesNotContainKey("artifactUploadToken")
                .doesNotContainKey("runtimeConfigUrl");
        verify(skillFileService).getSkillImportsByIds(List.of(42L), "approval-user", 200L);
        verify(skillFileService, never()).getSkillImportsByIds(List.of(42L));
    }

    @Test
    void removesClientSandboxWhenAuthoritativeSandboxIsDisabled() {
        SkillImportDto skill = new SkillImportDto();
        skill.setId(42L);
        when(skillFileService.getSkillImportsByIds(List.of(42L), "approval-user", 200L))
                .thenReturn(List.of(skill));
        SkillSandboxRuntimeRefDto disabledSandbox = new SkillSandboxRuntimeRefDto();
        disabledSandbox.setEnabled(Boolean.FALSE);
        when(skillSandboxConfigService.toRuntimeRefDto("approval-user", 200L))
                .thenReturn(disabledSandbox);
        JSONArray entries = new JSONArray(List.of(new JSONObject()
                .fluentPut("skillId", "42")
                .fluentPut("sandbox", new JSONObject().fluentPut("apiKey", "client-secret"))));

        skillEnrichmentService.enrichSkillEntries(entries, "approval-user", 200L);

        assertThat(entries.getJSONObject(0)).doesNotContainKey("sandbox");
    }

    @Test
    void removesClientSandboxWhenSkillImportIsMissing() {
        when(skillFileService.getSkillImportsByIds(List.of(42L), "approval-user", 200L))
                .thenReturn(List.of());
        JSONArray entries = new JSONArray(List.of(new JSONObject()
                .fluentPut("skillId", "42")
                .fluentPut("name", "attacker-name")
                .fluentPut("description", "attacker-description")
                .fluentPut("downloadUrl", "http://169.254.169.254/latest/meta-data")
                .fluentPut("download_url", "http://127.0.0.1/admin")
                .fluentPut("resources", List.of(new JSONObject()
                        .fluentPut("downloadUrl", "https://attacker.example/payload")))
                .fluentPut("sandbox", new JSONObject().fluentPut("apiKey", "client-secret"))));

        skillEnrichmentService.enrichSkillEntries(entries, "approval-user", 200L);

        assertThat(entries).isEmpty();
        verifyNoInteractions(skillSandboxConfigService);
    }

    @Test
    void removesClientSandboxBeforeReturningForInvalidOrMissingSkillIds() {
        JSONArray entries = new JSONArray(List.of(
                new JSONObject()
                        .fluentPut("skillId", "not-a-number")
                        .fluentPut("sandbox", new JSONObject().fluentPut("apiKey", "first-secret")),
                new JSONObject()
                        .fluentPut("sandbox", new JSONObject().fluentPut("apiKey", "second-secret"))));

        skillEnrichmentService.enrichSkillEntries(entries, "approval-user", 200L);

        assertThat(entries).isEmpty();
        verifyNoInteractions(skillFileService, skillSandboxConfigService);
    }

    @Test
    void removesUnauthorizedEntriesAndReplacesEveryClientDerivedRuntimeField() {
        SkillImportDto trusted = new SkillImportDto();
        trusted.setId(42L);
        trusted.setName("trusted-name");
        trusted.setDescription("trusted-description");
        trusted.setDownloadUrl("https://objects.example/trusted/skill.md?signature=server");
        trusted.setResources(List.of());
        when(skillFileService.getSkillImportsByIds(List.of(42L, 43L), "approval-user", 200L))
                .thenReturn(List.of(trusted));
        when(skillSandboxConfigService.toRuntimeRefDto("approval-user", 200L))
                .thenReturn(new SkillSandboxRuntimeRefDto());
        JSONObject attackerFields = new JSONObject()
                .fluentPut("name", "attacker-name")
                .fluentPut("description", "attacker-description")
                .fluentPut("downloadUrl", "http://169.254.169.254/latest/meta-data")
                .fluentPut("download_url", "http://127.0.0.1/admin")
                .fluentPut("resources", List.of(new JSONObject()
                        .fluentPut("downloadUrl", "https://attacker.example/resource")))
                .fluentPut("sandbox", new JSONObject()
                        .fluentPut("apiKey", "attacker-secret"));
        JSONArray entries = new JSONArray(List.of(
                new JSONObject(attackerFields).fluentPut("skillId", "42"),
                new JSONObject(attackerFields).fluentPut("skillId", "43")));

        skillEnrichmentService.enrichSkillEntries(entries, "approval-user", 200L);

        assertThat(entries).hasSize(1);
        assertThat(entries.getJSONObject(0))
                .containsEntry("skillId", "42")
                .containsEntry("name", "trusted-name")
                .containsEntry("description", "trusted-description")
                .containsEntry(
                        "downloadUrl",
                        "https://objects.example/trusted/skill.md?signature=server")
                .doesNotContainKey("download_url")
                .doesNotContainKey("sandbox");
        assertThat(entries.getJSONObject(0).getJSONArray("resources")).isEmpty();
    }
}
