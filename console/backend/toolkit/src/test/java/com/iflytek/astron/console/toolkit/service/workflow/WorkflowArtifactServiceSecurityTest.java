package com.iflytek.astron.console.toolkit.service.workflow;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.ArgumentMatchers.isNull;
import static org.mockito.Mockito.inOrder;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.core.MybatisConfiguration;
import com.baomidou.mybatisplus.core.metadata.TableInfoHelper;
import com.iflytek.astron.console.commons.constant.ResponseEnum;
import com.iflytek.astron.console.commons.config.JwtClaimsFilter;
import com.iflytek.astron.console.commons.entity.workflow.Workflow;
import com.iflytek.astron.console.commons.enums.space.SpaceRoleEnum;
import com.iflytek.astron.console.commons.exception.BusinessException;
import com.iflytek.astron.console.commons.service.space.SpaceUserService;
import com.iflytek.astron.console.commons.util.S3ClientUtil;
import com.iflytek.astron.console.toolkit.config.properties.SkillSandboxArtifactProperties;
import com.iflytek.astron.console.toolkit.entity.dto.workflow.WorkflowArtifactDto;
import com.iflytek.astron.console.toolkit.entity.table.workflow.WorkflowArtifact;
import com.iflytek.astron.console.toolkit.mapper.workflow.WorkflowArtifactMapper;
import com.iflytek.astron.console.toolkit.mapper.workflow.WorkflowMapper;
import com.iflytek.astron.console.toolkit.security.ArtifactUploadTokenProvider;
import com.iflytek.astron.console.toolkit.service.workflow.WorkflowArtifactFileValidator.ValidatedArtifact;
import com.iflytek.astron.console.toolkit.tool.DataPermissionCheckTool;
import java.io.InputStream;
import java.util.List;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import org.mockito.InOrder;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.apache.ibatis.builder.MapperBuilderAssistant;
import org.springframework.mock.web.MockMultipartFile;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.test.util.ReflectionTestUtils;
import org.springframework.util.unit.DataSize;
import org.springframework.web.context.request.RequestContextHolder;
import org.springframework.web.context.request.ServletRequestAttributes;

@ExtendWith(MockitoExtension.class)
class WorkflowArtifactServiceSecurityTest {

    private static final String BUCKET = "workflow-artifacts";

    @Mock
    private WorkflowArtifactMapper artifactMapper;

    @Mock
    private WorkflowMapper workflowMapper;

    @Mock
    private DataPermissionCheckTool dataPermissionCheckTool;

    @Mock
    private S3ClientUtil s3ClientUtil;

    @Mock
    private SpaceUserService spaceUserService;

    @Mock
    private ArtifactUploadTokenProvider tokenProvider;

    @Mock
    private WorkflowArtifactFileValidator fileValidator;

    private WorkflowArtifactService service;
    private SkillSandboxArtifactProperties properties;

    @BeforeEach
    void setUp() {
        TableInfoHelper.initTableInfo(
                new MapperBuilderAssistant(new MybatisConfiguration(), ""),
                WorkflowArtifact.class);
        service = new WorkflowArtifactService();
        properties = new SkillSandboxArtifactProperties();
        properties.setArtifactBucket(BUCKET);
        properties.setArtifactUploadToken("0123456789abcdef0123456789abcdef");
        ReflectionTestUtils.setField(service, "baseMapper", artifactMapper);
        ReflectionTestUtils.setField(service, "workflowMapper", workflowMapper);
        ReflectionTestUtils.setField(service, "dataPermissionCheckTool", dataPermissionCheckTool);
        ReflectionTestUtils.setField(service, "s3ClientUtil", s3ClientUtil);
        ReflectionTestUtils.setField(service, "spaceUserService", spaceUserService);
        ReflectionTestUtils.setField(service, "artifactProperties", properties);
        ReflectionTestUtils.setField(service, "artifactUploadTokenProvider", tokenProvider);
        ReflectionTestUtils.setField(service, "artifactFileValidator", fileValidator);
    }

    @AfterEach
    void tearDown() {
        RequestContextHolder.resetRequestAttributes();
    }

    @Test
    void rejectsInvalidServiceTokenBeforeResolvingWorkflow() {
        when(tokenProvider.matches("wrong")).thenReturn(false);

        assertBusinessError(
                () -> upload("wrong", "owner", null), ResponseEnum.UNAUTHORIZED);

        verify(workflowMapper, never()).selectOne(any());
        verify(s3ClientUtil, never()).uploadObject(
                any(), any(), any(), any(InputStream.class), eq(5L), eq(-1L));
    }

    @Test
    void refusesToReusePublicConsoleBucketForArtifacts() {
        when(s3ClientUtil.getDefaultBucket()).thenReturn(BUCKET);

        assertThatThrownBy(service::initializePrivateArtifactBucket)
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("separate");

        verify(s3ClientUtil, never()).ensurePrivateBucket(any());
    }

    @Test
    void rejectsForgedPersonalOwner() {
        allowTokenAndWorkflow(personalWorkflow("owner"));

        assertBusinessError(
                () -> upload("valid-token", "attacker", null),
                ResponseEnum.INSUFFICIENT_PERMISSIONS);

        verify(fileValidator, never()).validate(any());
        verify(s3ClientUtil, never()).uploadObject(
                any(), any(), any(), any(InputStream.class), eq(5L), eq(-1L));
    }

    @Test
    void directArtifactLookupIsScopedToCurrentPersonalOwnerEvenForPublicWorkflow() {
        bindRequest("attacker", null);
        when(artifactMapper.selectOne(any(), eq(false))).thenReturn(null);

        assertBusinessError(
                () -> service.getDownloadInfo(7L), ResponseEnum.DATA_NOT_EXIST);

        @SuppressWarnings("unchecked")
        ArgumentCaptor<LambdaQueryWrapper<WorkflowArtifact>> query =
                ArgumentCaptor.forClass(LambdaQueryWrapper.class);
        verify(artifactMapper).selectOne(query.capture(), eq(false));
        assertThat(query.getValue().getSqlSegment().toLowerCase())
                .contains("space_id is null")
                .contains("uid");
        assertThat(query.getValue().getParamNameValuePairs())
                .containsValue("attacker");
    }

    @Test
    void publicWorkflowDoesNotExposeTeamArtifactsToForgedSpaceContext() {
        bindRequest("outsider", 100L);
        Workflow publicWorkflow = personalWorkflow("owner");
        publicWorkflow.setSpaceId(100L);
        publicWorkflow.setIsPublic(Boolean.TRUE);
        when(workflowMapper.selectOne(any())).thenReturn(publicWorkflow);
        when(spaceUserService.getRole(100L, "outsider")).thenReturn(null);

        assertBusinessError(
                () -> service.listArtifacts(42L),
                ResponseEnum.INSUFFICIENT_PERMISSIONS);

        verify(artifactMapper, never()).selectList(any());
    }

    @Test
    void artifactListReturnsMetadataWithoutMintingDownloadUrls() {
        bindRequest("owner", null);
        Workflow workflow = personalWorkflow("owner");
        WorkflowArtifact artifact = artifact("owner", null, "workflow/artifacts/42/file.txt");
        when(workflowMapper.selectOne(any())).thenReturn(workflow);
        when(artifactMapper.selectList(any())).thenReturn(List.of(artifact));

        List<WorkflowArtifactDto> result = service.listArtifacts(42L);

        assertThat(result).singleElement().extracting(WorkflowArtifactDto::getDownloadUrl).isNull();
        verify(s3ClientUtil, never())
                .generatePresignedDownloadUrl(any(), any(), any(), any(Integer.class));
        @SuppressWarnings("unchecked")
        ArgumentCaptor<LambdaQueryWrapper<WorkflowArtifact>> query =
                ArgumentCaptor.forClass(LambdaQueryWrapper.class);
        verify(artifactMapper).selectList(query.capture());
        assertThat(query.getValue().getTargetSql().toLowerCase()).endsWith("limit 200");
    }

    @Test
    void deletedWorkflowCannotListOrMintArtifactUrls() {
        bindRequest("owner", null);
        when(workflowMapper.selectOne(any())).thenReturn(null);

        assertBusinessError(
                () -> service.listArtifacts(42L), ResponseEnum.WORKFLOW_NOT_EXIST);

        verify(artifactMapper, never()).selectList(any());
        verify(s3ClientUtil, never())
                .generatePresignedDownloadUrl(any(), any(), any(), any(Integer.class));
    }

    @Test
    void deletePhysicallyRemovesPrivateObjectAndCompletesTombstone() {
        bindRequest("owner", null);
        Workflow workflow = personalWorkflow("owner");
        WorkflowArtifact artifact = artifact("owner", null, "workflow/artifacts/42/file.txt");
        when(artifactMapper.selectOne(any(), eq(false))).thenReturn(artifact);
        when(workflowMapper.selectOne(any())).thenReturn(workflow);
        when(artifactMapper.updateById(any(WorkflowArtifact.class))).thenReturn(1);
        when(s3ClientUtil.removeObject(BUCKET, artifact.getObjectKey())).thenReturn(true);
        when(artifactMapper.update(isNull(), any())).thenReturn(1);

        service.deleteArtifact(artifact.getId());

        verify(s3ClientUtil).removeObject(BUCKET, artifact.getObjectKey());
        verify(artifactMapper).update(isNull(), any());
    }

    @Test
    void publicWorkflowVisibilityNeverGrantsArtifactDeletePermission() {
        bindRequest("attacker", null);
        Workflow publicVictim = personalWorkflow("owner");
        publicVictim.setIsPublic(Boolean.TRUE);
        WorkflowArtifact attackerScopedRow =
                artifact("attacker", null, "workflow/artifacts/42/file.txt");
        when(artifactMapper.selectOne(any(), eq(false))).thenReturn(attackerScopedRow);
        when(workflowMapper.selectOne(any())).thenReturn(publicVictim);

        assertBusinessError(
                () -> service.deleteArtifact(attackerScopedRow.getId()),
                ResponseEnum.INSUFFICIENT_PERMISSIONS);

        verify(artifactMapper, never()).updateById(any(WorkflowArtifact.class));
        verify(s3ClientUtil, never()).removeObject(any(), any());
    }

    @Test
    void failedPhysicalDeletionRemainsPersistedForScheduledRetry() {
        bindRequest("owner", null);
        Workflow workflow = personalWorkflow("owner");
        WorkflowArtifact artifact = artifact("owner", null, "workflow/artifacts/42/retry.txt");
        when(artifactMapper.selectOne(any(), eq(false))).thenReturn(artifact);
        when(workflowMapper.selectOne(any())).thenReturn(workflow);
        when(artifactMapper.updateById(any(WorkflowArtifact.class))).thenReturn(1);
        when(s3ClientUtil.removeObject(BUCKET, artifact.getObjectKey()))
                .thenReturn(false, true);
        when(artifactMapper.selectList(any())).thenReturn(List.of(artifact));
        when(artifactMapper.update(isNull(), any())).thenReturn(1);

        service.deleteArtifact(artifact.getId());
        service.purgeDeletedArtifactObjects();

        verify(s3ClientUtil, times(2)).removeObject(BUCKET, artifact.getObjectKey());
        verify(artifactMapper).update(isNull(), any());
    }

    @Test
    void rejectsForgedTeamMembership() {
        Workflow workflow = personalWorkflow("space-owner");
        workflow.setSpaceId(100L);
        allowTokenAndWorkflow(workflow);
        when(spaceUserService.getRole(100L, "former-member")).thenReturn(null);

        assertBusinessError(
                () -> upload("valid-token", "former-member", 100L),
                ResponseEnum.INSUFFICIENT_PERMISSIONS);

        verify(fileValidator, never()).validate(any());
        verify(s3ClientUtil, never()).uploadObject(
                any(), any(), any(), any(InputStream.class), eq(5L), eq(-1L));
    }

    @Test
    void storesValidatedArtifactInDedicatedPrivateBucket() {
        Workflow workflow = personalWorkflow("space-owner");
        workflow.setSpaceId(100L);
        allowTokenAndWorkflow(workflow);
        allowQuotaLock(workflow);
        when(spaceUserService.getRole(100L, "member")).thenReturn(SpaceRoleEnum.MEMBER);
        when(fileValidator.validate(any()))
                .thenReturn(new ValidatedArtifact("result.txt", "text/plain"));
        when(artifactMapper.insert(any(WorkflowArtifact.class))).thenReturn(1);
        WorkflowArtifactDto result = upload("valid-token", "member", 100L);

        ArgumentCaptor<String> objectKey = ArgumentCaptor.forClass(String.class);
        verify(s3ClientUtil).uploadObject(
                eq(BUCKET),
                objectKey.capture(),
                eq("text/plain"),
                any(InputStream.class),
                eq(5L),
                eq(-1L));
        ArgumentCaptor<WorkflowArtifact> artifact = ArgumentCaptor.forClass(WorkflowArtifact.class);
        verify(artifactMapper).insert(artifact.capture());
        assertThat(artifact.getValue())
                .extracting(
                        WorkflowArtifact::getUid,
                        WorkflowArtifact::getSpaceId,
                        WorkflowArtifact::getWorkflowId,
                        WorkflowArtifact::getBucketName,
                        WorkflowArtifact::getContentType)
                .containsExactly("member", 100L, 42L, BUCKET, "text/plain");
        assertThat(objectKey.getValue())
                .startsWith("workflow/artifacts/42/run-1/")
                .endsWith("_result.txt");
        verify(s3ClientUtil, never())
                .generatePresignedDownloadUrl(any(), any(), any(), any(Integer.class));
        assertThat(result.getDownloadUrl()).isNull();

        InOrder quotaBeforeUpload = inOrder(workflowMapper, artifactMapper, s3ClientUtil);
        quotaBeforeUpload
                .verify(workflowMapper)
                .selectActiveByIdForArtifactQuotaLock(workflow.getId());
        quotaBeforeUpload.verify(artifactMapper).countActiveByWorkflowId(workflow.getId());
        quotaBeforeUpload.verify(artifactMapper).sumActiveBytesByWorkflowId(workflow.getId());
        quotaBeforeUpload
                .verify(s3ClientUtil)
                .uploadObject(
                        eq(BUCKET),
                        any(),
                        eq("text/plain"),
                        any(InputStream.class),
                        eq(5L),
                        eq(-1L));
    }

    @Test
    void generatedObjectKeyNeverContainsRelativeRunSegment() {
        String objectKey = ReflectionTestUtils.invokeMethod(
                service, "buildObjectKey", 42L, "..", "result.txt");

        assertThat(objectKey)
                .startsWith("workflow/artifacts/42/_/")
                .endsWith("_result.txt");
    }

    @Test
    void activeFileCountQuotaRejectsBeforeObjectUpload() {
        Workflow workflow = personalWorkflow("owner");
        allowTokenAndWorkflow(workflow);
        allowQuotaLock(workflow);
        when(fileValidator.validate(any()))
                .thenReturn(new ValidatedArtifact("result.txt", "text/plain"));
        when(artifactMapper.countActiveByWorkflowId(42L)).thenReturn(1000L);

        assertBusinessError(
                () -> upload("valid-token", "owner", null),
                ResponseEnum.WORKFLOW_ARTIFACT_QUOTA_EXCEEDED);

        verify(s3ClientUtil, never()).uploadObject(
                any(), any(), any(), any(InputStream.class), eq(5L), eq(-1L));
        verify(artifactMapper, never()).insert(any(WorkflowArtifact.class));
    }

    @Test
    void activeTotalByteQuotaRejectsBeforeObjectUpload() {
        Workflow workflow = personalWorkflow("owner");
        allowTokenAndWorkflow(workflow);
        allowQuotaLock(workflow);
        properties.setArtifactMaxActiveTotalSizePerWorkflow(DataSize.ofBytes(9));
        when(fileValidator.validate(any()))
                .thenReturn(new ValidatedArtifact("result.txt", "text/plain"));
        when(artifactMapper.sumActiveBytesByWorkflowId(42L)).thenReturn(5L);

        assertBusinessError(
                () -> upload("valid-token", "owner", null),
                ResponseEnum.WORKFLOW_ARTIFACT_QUOTA_EXCEEDED);

        verify(s3ClientUtil, never()).uploadObject(
                any(), any(), any(), any(InputStream.class), eq(5L), eq(-1L));
        verify(artifactMapper, never()).insert(any(WorkflowArtifact.class));
    }

    @Test
    void deletedWorkflowCannotUploadAfterWaitingForQuotaLock() {
        Workflow workflow = personalWorkflow("owner");
        allowTokenAndWorkflow(workflow);
        when(fileValidator.validate(any()))
                .thenReturn(new ValidatedArtifact("result.txt", "text/plain"));
        when(workflowMapper.selectActiveByIdForArtifactQuotaLock(42L)).thenReturn(null);

        assertBusinessError(
                () -> upload("valid-token", "owner", null), ResponseEnum.WORKFLOW_NOT_EXIST);

        verify(artifactMapper, never()).countActiveByWorkflowId(any());
        verify(s3ClientUtil, never()).uploadObject(
                any(), any(), any(), any(InputStream.class), eq(5L), eq(-1L));
    }

    @Test
    void tombstonesEveryActiveArtifactForDeletedWorkflow() {
        when(artifactMapper.update(isNull(), any())).thenReturn(3);

        int tombstoned = service.tombstoneWorkflowArtifacts(42L);

        assertThat(tombstoned).isEqualTo(3);
        @SuppressWarnings("unchecked")
        ArgumentCaptor<com.baomidou.mybatisplus.core.conditions.update.LambdaUpdateWrapper<
                        WorkflowArtifact>>
                update = ArgumentCaptor.forClass(
                        com.baomidou.mybatisplus.core.conditions.update.LambdaUpdateWrapper.class);
        verify(artifactMapper).update(isNull(), update.capture());
        assertThat(update.getValue().getTargetSql().toLowerCase())
                .contains("workflow_id")
                .contains("deleted");
        assertThat(update.getValue().getParamNameValuePairs()).containsValue(42L);
    }

    @Test
    void removesUploadedObjectWhenDatabaseInsertFails() {
        Workflow workflow = personalWorkflow("owner");
        allowTokenAndWorkflow(workflow);
        allowQuotaLock(workflow);
        when(fileValidator.validate(any()))
                .thenReturn(new ValidatedArtifact("result.txt", "text/plain"));
        when(artifactMapper.insert(any(WorkflowArtifact.class)))
                .thenThrow(new IllegalStateException("database unavailable"));
        when(s3ClientUtil.removeObject(eq(BUCKET), any())).thenReturn(true);

        assertThatThrownBy(() -> upload("valid-token", "owner", null))
                .isInstanceOf(IllegalStateException.class)
                .hasMessage("database unavailable");

        ArgumentCaptor<String> uploadedKey = ArgumentCaptor.forClass(String.class);
        verify(s3ClientUtil).uploadObject(
                eq(BUCKET),
                uploadedKey.capture(),
                eq("text/plain"),
                any(InputStream.class),
                eq(5L),
                eq(-1L));
        verify(s3ClientUtil).removeObject(BUCKET, uploadedKey.getValue());
    }

    @Test
    void legacyArtifactDownloadFallsBackToOriginalBucketButStillForcesAttachment() {
        WorkflowArtifact artifact = new WorkflowArtifact();
        artifact.setWorkflowId(42L);
        artifact.setObjectKey("workflow/artifacts/42/legacy/file.txt");
        artifact.setFileName("file.txt");
        when(s3ClientUtil.getDefaultBucket()).thenReturn("console-oss");
        when(s3ClientUtil.generatePresignedDownloadUrl(any(), any(), any(), eq(300)))
                .thenReturn("https://download.example/legacy");

        WorkflowArtifactDto result =
                ReflectionTestUtils.invokeMethod(service, "toDto", artifact, true);

        verify(s3ClientUtil).generatePresignedDownloadUrl(
                "console-oss", "workflow/artifacts/42/legacy/file.txt", "file.txt", 300);
        assertThat(result.getDownloadUrl()).isEqualTo("https://download.example/legacy");
    }

    @Test
    void downloadRejectsObjectKeyOutsideOwningWorkflowPrefix() {
        bindRequest("owner", null);
        WorkflowArtifact artifact = artifact(
                "owner", null, "workflow/artifacts/99/foreign/file.txt");
        when(artifactMapper.selectOne(any(), eq(false))).thenReturn(artifact);
        when(workflowMapper.selectOne(any())).thenReturn(personalWorkflow("owner"));

        assertBusinessError(
                () -> service.getDownloadInfo(artifact.getId()), ResponseEnum.DATA_NOT_EXIST);

        verify(s3ClientUtil, never())
                .generatePresignedDownloadUrl(any(), any(), any(), any(Integer.class));
    }

    @ParameterizedTest
    @ValueSource(
            strings = {
                    "workflow/artifacts/42/run\\foreign.txt",
                    "workflow/artifacts/42/run/\nforeign.txt",
                    "workflow/artifacts/42//foreign.txt",
                    "workflow/artifacts/42/./foreign.txt",
                    "workflow/artifacts/42/../foreign.txt"
            })
    void downloadRejectsNonCanonicalObjectKey(String objectKey) {
        bindRequest("owner", null);
        WorkflowArtifact artifact = artifact("owner", null, objectKey);
        when(artifactMapper.selectOne(any(), eq(false))).thenReturn(artifact);
        when(workflowMapper.selectOne(any())).thenReturn(personalWorkflow("owner"));

        assertBusinessError(
                () -> service.getDownloadInfo(artifact.getId()), ResponseEnum.DATA_NOT_EXIST);

        verify(s3ClientUtil, never())
                .generatePresignedDownloadUrl(any(), any(), any(), any(Integer.class));
    }

    @Test
    void downloadRejectsUnconfiguredArtifactBucket() {
        bindRequest("owner", null);
        WorkflowArtifact artifact = artifact(
                "owner", null, "workflow/artifacts/42/run/file.txt");
        artifact.setBucketName("attacker-controlled-bucket");
        when(artifactMapper.selectOne(any(), eq(false))).thenReturn(artifact);
        when(workflowMapper.selectOne(any())).thenReturn(personalWorkflow("owner"));

        assertBusinessError(
                () -> service.getDownloadInfo(artifact.getId()), ResponseEnum.DATA_NOT_EXIST);

        verify(s3ClientUtil, never())
                .generatePresignedDownloadUrl(any(), any(), any(), any(Integer.class));
    }

    @Test
    void deleteRejectsCorruptStoragePointerBeforeTombstoningOrS3Access() {
        bindRequest("owner", null);
        WorkflowArtifact artifact = artifact("owner", null, "shared/foreign-object");
        when(artifactMapper.selectOne(any(), eq(false))).thenReturn(artifact);
        when(workflowMapper.selectOne(any())).thenReturn(personalWorkflow("owner"));

        assertBusinessError(
                () -> service.deleteArtifact(artifact.getId()), ResponseEnum.DATA_NOT_EXIST);

        verify(artifactMapper, never()).updateById(any(WorkflowArtifact.class));
        verify(s3ClientUtil, never()).removeObject(any(), any());
    }

    @Test
    void scheduledPurgeSkipsForeignKeysAndUnconfiguredBuckets() {
        WorkflowArtifact foreignKey = artifact("owner", null, "workflow/artifacts/99/file.txt");
        foreignKey.setDeleted(Boolean.TRUE);
        WorkflowArtifact foreignBucket = artifact(
                "owner", null, "workflow/artifacts/42/run/file.txt");
        foreignBucket.setId(8L);
        foreignBucket.setBucketName("attacker-controlled-bucket");
        foreignBucket.setDeleted(Boolean.TRUE);
        when(artifactMapper.selectList(any())).thenReturn(List.of(foreignKey, foreignBucket));

        service.purgeDeletedArtifactObjects();

        verify(s3ClientUtil, never()).removeObject(any(), any());
        verify(artifactMapper, never()).update(isNull(), any());
    }

    @Test
    void legacyMigrationSkipsObjectOutsideOwningWorkflowPrefix() {
        WorkflowArtifact legacy = new WorkflowArtifact();
        legacy.setId(9L);
        legacy.setWorkflowId(42L);
        legacy.setObjectKey("workflow/artifacts/99/foreign/file.txt");
        when(s3ClientUtil.getDefaultBucket()).thenReturn("console-oss");
        when(artifactMapper.selectList(any())).thenReturn(List.of(legacy), List.of());
        when(s3ClientUtil.listObjectKeys(
                "console-oss", "workflow/artifacts/", null, 100))
                .thenReturn(List.of());

        service.migrateLegacyArtifactStorage();

        verify(s3ClientUtil, never()).copyObject(any(), any(), any(), any());
        verify(s3ClientUtil, never()).removeObject(any(), any());
        verify(artifactMapper, never()).update(isNull(), any());
    }

    @Test
    void migratesLegacyArtifactOutOfPublicBucketBeforeDeletingSource() {
        WorkflowArtifact legacy = new WorkflowArtifact();
        legacy.setId(7L);
        legacy.setWorkflowId(42L);
        legacy.setObjectKey("workflow/artifacts/42/legacy/file.txt");
        when(s3ClientUtil.getDefaultBucket()).thenReturn("console-oss");
        when(artifactMapper.selectList(any())).thenReturn(List.of(legacy), List.of());
        when(s3ClientUtil.listObjectKeys(
                "console-oss", "workflow/artifacts/", null, 100))
                .thenReturn(List.of());
        when(s3ClientUtil.copyObject(
                "console-oss", legacy.getObjectKey(), BUCKET, legacy.getObjectKey()))
                .thenReturn(true);
        when(artifactMapper.update(isNull(), any())).thenReturn(1);
        when(s3ClientUtil.removeObject("console-oss", legacy.getObjectKey())).thenReturn(true);

        service.migrateLegacyArtifactStorage();

        verify(s3ClientUtil).copyObject(
                "console-oss", legacy.getObjectKey(), BUCKET, legacy.getObjectKey());
        verify(artifactMapper).update(isNull(), any());
        verify(s3ClientUtil).removeObject("console-oss", legacy.getObjectKey());
    }

    @Test
    void keepsPublicSourceAndDatabasePointerWhenLegacyCopyFails() {
        WorkflowArtifact legacy = new WorkflowArtifact();
        legacy.setId(8L);
        legacy.setWorkflowId(42L);
        legacy.setObjectKey("workflow/artifacts/42/legacy/unavailable.txt");
        when(s3ClientUtil.getDefaultBucket()).thenReturn("console-oss");
        when(s3ClientUtil.listObjectKeys(
                "console-oss", "workflow/artifacts/", null, 100))
                .thenReturn(List.of(legacy.getObjectKey()));
        when(artifactMapper.selectList(any()))
                .thenReturn(List.of(legacy), List.of(), List.of(legacy));
        when(s3ClientUtil.copyObject(
                "console-oss", legacy.getObjectKey(), BUCKET, legacy.getObjectKey()))
                .thenReturn(false);

        service.migrateLegacyArtifactStorage();

        verify(artifactMapper, never()).update(any(), any());
        verify(s3ClientUtil, never()).removeObject("console-oss", legacy.getObjectKey());
    }

    @Test
    void retriesPublicSourceDeletionAfterDatabasePointerWasAlreadyMigrated() {
        String objectKey = "workflow/artifacts/42/legacy/delete-retry.txt";
        when(s3ClientUtil.getDefaultBucket()).thenReturn("console-oss");
        when(artifactMapper.selectList(any())).thenReturn(List.of());
        when(s3ClientUtil.listObjectKeys(
                "console-oss", "workflow/artifacts/", null, 100))
                .thenReturn(List.of(objectKey));
        when(s3ClientUtil.removeObject("console-oss", objectKey)).thenReturn(true);

        service.migrateLegacyArtifactStorage();

        verify(s3ClientUtil).removeObject("console-oss", objectKey);
    }

    @Test
    void removesOrphanedPublicArtifactWithoutDatabaseRecord() {
        String orphanKey = "workflow/artifacts/42/orphan/known-malicious.html";
        when(s3ClientUtil.getDefaultBucket()).thenReturn("console-oss");
        when(artifactMapper.selectList(any())).thenReturn(List.of());
        when(s3ClientUtil.listObjectKeys(
                "console-oss", "workflow/artifacts/", null, 100))
                .thenReturn(List.of(orphanKey));
        when(s3ClientUtil.removeObject("console-oss", orphanKey)).thenReturn(true);

        service.migrateLegacyArtifactStorage();

        verify(s3ClientUtil, times(1)).removeObject("console-oss", orphanKey);
    }

    private WorkflowArtifactDto upload(String token, String uid, Long spaceId) {
        MockMultipartFile file =
                new MockMultipartFile("file", "result.txt", "text/plain", "hello".getBytes());
        return service.uploadInternal(
                token,
                null,
                "flow-1",
                uid,
                spaceId,
                "run-1",
                "node-1",
                "skill-1",
                "skill_sandbox",
                file);
    }

    private void allowTokenAndWorkflow(Workflow workflow) {
        when(tokenProvider.matches("valid-token")).thenReturn(true);
        when(workflowMapper.selectOne(any())).thenReturn(workflow);
    }

    private void allowQuotaLock(Workflow workflow) {
        when(workflowMapper.selectActiveByIdForArtifactQuotaLock(workflow.getId()))
                .thenReturn(workflow);
    }

    private Workflow personalWorkflow(String uid) {
        Workflow workflow = new Workflow();
        workflow.setId(42L);
        workflow.setFlowId("flow-1");
        workflow.setUid(uid);
        workflow.setDeleted(Boolean.FALSE);
        return workflow;
    }

    private WorkflowArtifact artifact(String uid, Long spaceId, String objectKey) {
        WorkflowArtifact artifact = new WorkflowArtifact();
        artifact.setId(7L);
        artifact.setUid(uid);
        artifact.setSpaceId(spaceId);
        artifact.setWorkflowId(42L);
        artifact.setFileName("file.txt");
        artifact.setObjectKey(objectKey);
        artifact.setBucketName(BUCKET);
        artifact.setDeleted(Boolean.FALSE);
        return artifact;
    }

    private void bindRequest(String uid, Long spaceId) {
        MockHttpServletRequest request = new MockHttpServletRequest();
        request.setAttribute(JwtClaimsFilter.USER_ID_ATTRIBUTE, uid);
        if (spaceId != null) {
            request.addHeader("space-id", String.valueOf(spaceId));
        }
        RequestContextHolder.setRequestAttributes(new ServletRequestAttributes(request));
    }

    private void assertBusinessError(Runnable action, ResponseEnum expected) {
        assertThatThrownBy(action::run)
                .isInstanceOf(BusinessException.class)
                .extracting("responseEnum")
                .isEqualTo(expected);
    }
}
