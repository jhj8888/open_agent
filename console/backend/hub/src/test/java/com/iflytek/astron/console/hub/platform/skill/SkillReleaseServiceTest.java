package com.iflytek.astron.console.hub.platform.skill;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.iflytek.astron.console.commons.config.JwtClaimsFilter;
import com.iflytek.astron.console.commons.tenant.*;
import java.nio.charset.StandardCharsets;
import java.util.*;
import org.junit.jupiter.api.*;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.mock.env.MockEnvironment;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.support.SimpleTransactionStatus;
import org.springframework.web.context.request.*;

class SkillReleaseServiceTest {
    private final JdbcTemplate jdbc=mock(JdbcTemplate.class);
    private final TenantAccessService access=mock(TenantAccessService.class);
    private final SkillArtifactStore store=mock(SkillArtifactStore.class);
    private final PlatformTransactionManager manager=mock(PlatformTransactionManager.class);
    private final ObjectMapper json=new ObjectMapper();
    private final String image="registry.internal/skill@sha256:"+"a".repeat(64);
    private final MockEnvironment env=new MockEnvironment().withProperty("SKILL_RUNTIME_IMAGES","[\""+image+"\"]");
    private final SkillReleaseService service=new SkillReleaseService(jdbc,access,store,manager,json,env);
    private final List<String> events=new ArrayList<>();
    private String upload,prefix;
    private boolean failed;
    private List<Map<String,Object>> existing=List.of();
    private SkillArchiveValidator.Validated sample() {
        byte[] bytes="---\nname: test\ndescription: test skill\n---\nHello".getBytes(StandardCharsets.UTF_8);
        return new SkillArchiveValidator.Validated("test","test skill",List.of(new SkillArchiveValidator.FileContent("SKILL.md",bytes,SkillArchiveValidator.sha256(bytes))),bytes.length);
    }
    @BeforeEach void setup() {
        var request=new MockHttpServletRequest();request.setAttribute(JwtClaimsFilter.USER_ID_ATTRIBUTE,"alice");
        RequestContextHolder.setRequestAttributes(new ServletRequestAttributes(request));
        when(manager.getTransaction(any())).thenReturn(new SimpleTransactionStatus());
        when(jdbc.queryForList(anyString(),any(Object[].class))).thenAnswer(call->{
            String sql=call.getArgument(0);
            if(sql.startsWith("SELECT tenant_id FROM oa_tenant")) return List.of(Map.of("tenant_id","space-10"));
            if(sql.startsWith("SELECT upload_id FROM oa_skill_upload")) return List.of(Map.of("upload_id",upload));
            if(sql.startsWith("SELECT object_prefix FROM oa_skill_upload")) return failed?List.of(Map.of("object_prefix",prefix)):List.of();
            if(sql.startsWith("SELECT release_id,manifest_sha256,status")) return existing;
            return List.of();
        });
        when(jdbc.update(anyString(),any(Object[].class))).thenAnswer(call->{
            String sql=call.getArgument(0);events.add(sql);
            if(sql.startsWith("INSERT INTO oa_skill_upload")) {upload=call.getArgument(3);prefix=call.getArgument(4);}
            if(sql.startsWith("UPDATE oa_skill_upload SET state='failed'")) failed=true;
            return 1;
        });
        doAnswer(call->{events.add("object-written");return null;}).when(store).put(anyString(),any());
    }
    @AfterEach void close() {RequestContextHolder.resetRequestAttributes();}

    @Test void commitsManifestOnlyAfterObjectUploadAndNeverReturnsStorageKeys() {
        var result=service.publishValidated("space-10","test","v1",image,sample(),null);
        assertEquals(upload,result.get("releaseId"));assertEquals(false,result.get("reused"));
        assertEquals(Set.of("releaseId","manifestSha256","reused"),result.keySet());
        int insert=java.util.stream.IntStream.range(0,events.size()).filter(i->events.get(i).startsWith("INSERT INTO oa_skill_release(")).findFirst().orElseThrow();
        assertTrue(events.indexOf("object-written")<insert);
        verify(access,times(2)).require("space-10","test",TenantPermission.RESOURCE_WRITE);
        verify(store,never()).removePrefix(anyString());
    }
    @Test void failedUploadNeverPublishesAndCleansOnlyItsPrivatePrefix() {
        doThrow(new MinioSkillArtifactStore.ArtifactUnavailableException()).when(store).put(anyString(),any());
        assertThrows(MinioSkillArtifactStore.ArtifactUnavailableException.class,()->service.publishValidated("space-10","test","v1",image,sample(),null));
        assertTrue(events.stream().noneMatch(sql->sql.startsWith("INSERT INTO oa_skill_release(")));
        verify(store).removePrefix(SkillArtifactStore.prefix("space-10",upload));
    }
    @Test void identicalRetryReusesReleaseAndCleansItsNewStagingObjects() {
        existing=List.of(Map.of("release_id","previous","manifest_sha256",SkillReleaseManifest.encode(json,sample(),"v1",image).sha256(),"status","ready"));
        var result=service.publishValidated("space-10","test","v1",image,sample(),null);
        assertEquals("previous",result.get("releaseId"));assertEquals(true,result.get("reused"));
        verify(store).removePrefix(prefix);
        assertTrue(events.stream().noneMatch(sql->sql.startsWith("INSERT INTO oa_skill_release(")));
    }
    @Test void failureAfterOneObjectWasStoredStillCleansTheEntireUpload() {
        var files=new ArrayList<>(sample().files());
        byte[] bytes="print('test')".getBytes(StandardCharsets.UTF_8);
        files.add(new SkillArchiveValidator.FileContent("scripts/test.py",bytes,SkillArchiveValidator.sha256(bytes)));
        var packageFiles=new SkillArchiveValidator.Validated("test","test skill",files,sample().totalBytes()+bytes.length);
        doNothing().doThrow(new MinioSkillArtifactStore.ArtifactUnavailableException()).when(store).put(anyString(),any());
        assertThrows(MinioSkillArtifactStore.ArtifactUnavailableException.class,()->service.publishValidated("space-10","test","v1",image,packageFiles,null));
        verify(store,times(2)).put(anyString(),any());
        verify(store).removePrefix(prefix);
        assertTrue(events.stream().noneMatch(sql->sql.startsWith("INSERT INTO oa_skill_release(")));
    }
    @Test void sameVersionCannotBeOverwrittenWithDifferentContent() {
        existing=List.of(Map.of("release_id","previous","manifest_sha256","b".repeat(64),"status","ready"));
        assertThrows(SkillReleaseService.ReleaseConflictException.class,()->service.publishValidated("space-10","test","v1",image,sample(),null));
        verify(store).removePrefix(prefix);
    }
    @Test void revokedMembershipDuringUploadPreventsPublication() {
        when(access.require("space-10","test",TenantPermission.RESOURCE_WRITE)).thenReturn(null).thenThrow(new TenantAccessDeniedException());
        assertThrows(TenantAccessDeniedException.class,()->service.publishValidated("space-10","test","v1",image,sample(),null));
        verify(store).removePrefix(prefix);
        assertTrue(events.stream().noneMatch(sql->sql.startsWith("INSERT INTO oa_skill_release(")));
    }
    @Test void crossTenantAndCrossEnvironmentDownloadsNeverTouchObjectStorage() {
        assertThrows(TenantAccessDeniedException.class,()->service.download("space-20","test","release-of-tenant-10",0));
        assertThrows(TenantAccessDeniedException.class,()->service.download("space-10","prod","test-only-release",0));
        verifyNoInteractions(store);
    }
    @Test void missingMembershipStopsBeforeDatabaseOrStorageReads() {
        doThrow(new TenantAccessDeniedException()).when(access).require("space-10","test",TenantPermission.METADATA_READ);
        clearInvocations(jdbc);
        assertThrows(TenantAccessDeniedException.class,()->service.download("space-10","test","release",0));
        verifyNoInteractions(store,jdbc);
    }
    @Test void productionPromotionRequiresOperatorAndSuccessfulEvidence() {
        assertThrows(SkillReleaseService.ReleaseConflictException.class,()->service.promote("space-10","release"));
        verify(access).require("space-10","prod",TenantPermission.DEPLOYMENT_PUBLISH);
        assertTrue(events.isEmpty());
        doThrow(new TenantAccessDeniedException()).when(access).require("space-10","prod",TenantPermission.DEPLOYMENT_PUBLISH);
        assertThrows(TenantAccessDeniedException.class,()->service.promote("space-10","release"));
        verifyNoInteractions(store);
    }
    @Test void referencedOrRetainedReleaseCannotBeDeleted() {
        when(jdbc.queryForList(startsWith("SELECT r.release_id"),any(Object[].class))).thenReturn(List.of(Map.of("release_id","release")));
        when(jdbc.update(startsWith("UPDATE oa_skill_release r SET status='deleted'"),any(Object[].class))).thenReturn(0);
        assertThrows(SkillReleaseService.ReleaseConflictException.class,()->service.delete("space-10","test","release"));
        verifyNoInteractions(store);
    }
    @Test void successfulPromotionGrantsTheOriginalReleaseAndAuditsItsDigest() {
        when(jdbc.queryForList(startsWith("SELECT r.manifest_sha256"),any(Object[].class)))
                .thenReturn(List.of(Map.of("manifest_sha256","a".repeat(64))));
        service.promote("space-10","release");
        verify(jdbc).update(startsWith("INSERT INTO oa_skill_release_environment"),eq("space-10"),eq("prod"),eq("release"),eq("alice"));
        verify(jdbc).update(startsWith("INSERT INTO oa_tenant_audit"),eq("alice"),eq("skill.release.promoted"),eq("release"),eq("a".repeat(64)),eq("space-10"),eq("prod"));
        verifyNoInteractions(store);
    }
    @Test void approvedImagesRequireImmutableDigestsAndFailClosed() {
        assertEquals(List.of(image),service.images("space-10","test"));
        env.setProperty("SKILL_RUNTIME_IMAGES","[\"registry.internal/skill:latest\"]");
        assertThrows(IllegalStateException.class,()->service.images("space-10","test"));
        assertThrows(IllegalArgumentException.class,()->SkillArtifactStore.checkPrefix(SkillArtifactStore.ROOT));
        assertThrows(IllegalArgumentException.class,()->SkillArtifactStore.checkKey("skill-files/tenant/secret"));
    }
    @Test void manifestDigestBindsContentVersionAndRuntimeImage() {
        var value=SkillReleaseManifest.encode(json,sample(),"v1",image);
        assertEquals(value,SkillReleaseManifest.encode(json,sample(),"v1",image));
        assertNotEquals(value.sha256(),SkillReleaseManifest.encode(json,sample(),"v2",image).sha256());
        assertNotEquals(value.sha256(),SkillReleaseManifest.encode(json,sample(),"v1",image.replace("sha256:a","sha256:b")).sha256());
        assertEquals(value.sha256(),SkillArchiveValidator.sha256(value.json().getBytes(StandardCharsets.UTF_8)));
        assertFalse(value.json().contains("object_key"));
    }
}
