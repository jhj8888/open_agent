package com.iflytek.astron.console.toolkit.service.workflow;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.core.toolkit.Wrappers;
import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import com.iflytek.astron.console.commons.constant.ResponseEnum;
import com.iflytek.astron.console.commons.entity.workflow.Workflow;
import com.iflytek.astron.console.commons.exception.BusinessException;
import com.iflytek.astron.console.commons.service.space.SpaceUserService;
import com.iflytek.astron.console.commons.util.S3ClientUtil;
import com.iflytek.astron.console.commons.util.space.SpaceInfoUtil;
import com.iflytek.astron.console.toolkit.config.properties.SkillSandboxArtifactProperties;
import com.iflytek.astron.console.toolkit.entity.dto.workflow.WorkflowArtifactDto;
import com.iflytek.astron.console.toolkit.entity.table.workflow.WorkflowArtifact;
import com.iflytek.astron.console.toolkit.handler.UserInfoManagerHandler;
import com.iflytek.astron.console.toolkit.mapper.workflow.WorkflowArtifactMapper;
import com.iflytek.astron.console.toolkit.mapper.workflow.WorkflowMapper;
import com.iflytek.astron.console.toolkit.security.ArtifactUploadTokenProvider;
import com.iflytek.astron.console.toolkit.service.workflow.WorkflowArtifactFileValidator.ValidatedArtifact;
import com.iflytek.astron.console.toolkit.tool.DataPermissionCheckTool;
import jakarta.annotation.PostConstruct;
import jakarta.annotation.Resource;
import java.io.InputStream;
import java.time.LocalDateTime;
import java.util.HashSet;
import java.util.List;
import java.util.Objects;
import java.util.Set;
import java.util.UUID;
import lombok.extern.slf4j.Slf4j;
import org.apache.commons.lang3.StringUtils;
import org.springframework.context.event.EventListener;
import org.springframework.boot.context.event.ApplicationReadyEvent;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;
import org.springframework.web.multipart.MultipartFile;

@Service
@Slf4j
public class WorkflowArtifactService
        extends ServiceImpl<WorkflowArtifactMapper, WorkflowArtifact> {

    private static final int LEGACY_MIGRATION_BATCH_SIZE = 100;
    private static final int LEGACY_PUBLIC_CLEANUP_BATCH_SIZE = 100;
    private static final int ARTIFACT_PURGE_BATCH_SIZE = 100;
    private static final int ARTIFACT_LIST_RESULT_LIMIT = 200;
    private static final String LEGACY_PUBLIC_ARTIFACT_PREFIX = "workflow/artifacts/";

    @Resource
    private WorkflowMapper workflowMapper;

    @Resource
    private DataPermissionCheckTool dataPermissionCheckTool;

    @Resource
    private S3ClientUtil s3ClientUtil;

    @Resource
    private SpaceUserService spaceUserService;

    @Resource
    private SkillSandboxArtifactProperties artifactProperties;

    @Resource
    private ArtifactUploadTokenProvider artifactUploadTokenProvider;

    @Resource
    private WorkflowArtifactFileValidator artifactFileValidator;

    private String legacyPublicCleanupCursor;

    @PostConstruct
    void initializePrivateArtifactBucket() {
        String artifactBucket = StringUtils.trimToEmpty(artifactProperties.getArtifactBucket());
        if (StringUtils.equals(artifactBucket, s3ClientUtil.getDefaultBucket())) {
            throw new IllegalStateException(
                    "Workflow artifacts must use a bucket separate from the shared console bucket");
        }
        s3ClientUtil.ensurePrivateBucket(artifactBucket);
    }

    /** Move artifacts created before the private-bucket migration out of the public console bucket. */
    @EventListener(ApplicationReadyEvent.class)
    @Scheduled(
            fixedDelayString = "${skill.sandbox.artifact-legacy-reconcile-interval-ms:300000}",
            initialDelayString = "${skill.sandbox.artifact-legacy-reconcile-initial-delay-ms:300000}")
    public synchronized void migrateLegacyArtifactStorage() {
        LegacyReferenceMigrationResult migration = migrateLegacyArtifactReferences();
        LegacyPublicCleanupResult cleanup = cleanupLegacyPublicArtifacts();
        int failed = migration.failed() + cleanup.failed();
        if (migration.migrated() > 0 || failed > 0 || cleanup.cleaned() > 0 || cleanup.retained() > 0) {
            log.info(
                    "Legacy workflow artifact storage reconciliation finished, migrated={}, cleaned={}, retained={}, failed={}",
                    migration.migrated(),
                    cleanup.cleaned(),
                    cleanup.retained(),
                    failed);
        }
    }

    private LegacyReferenceMigrationResult migrateLegacyArtifactReferences() {
        String targetBucket = artifactProperties.getArtifactBucket();
        Long lastId = null;
        int migrated = 0;
        int failed = 0;
        while (true) {
            LambdaQueryWrapper<WorkflowArtifact> query = Wrappers.lambdaQuery(WorkflowArtifact.class)
                    .and(bucket -> bucket.isNull(WorkflowArtifact::getBucketName)
                            .or()
                            .eq(WorkflowArtifact::getBucketName, ""))
                    .isNotNull(WorkflowArtifact::getObjectKey)
                    .ne(WorkflowArtifact::getObjectKey, "")
                    .orderByAsc(WorkflowArtifact::getId)
                    .last("limit " + LEGACY_MIGRATION_BATCH_SIZE);
            if (lastId != null) {
                query.gt(WorkflowArtifact::getId, lastId);
            }
            List<WorkflowArtifact> batch = baseMapper.selectList(query);
            if (batch == null || batch.isEmpty()) {
                break;
            }
            for (WorkflowArtifact artifact : batch) {
                lastId = artifact.getId();
                ArtifactObjectLocation sourceLocation;
                try {
                    sourceLocation = requireArtifactObjectLocation(artifact);
                } catch (BusinessException exception) {
                    failed++;
                    log.error(
                            "Skipped legacy workflow artifact migration because its storage pointer is outside the artifact scope, artifactId={}",
                            artifact.getId());
                    continue;
                }
                String objectKey = sourceLocation.objectKey();
                if (!s3ClientUtil.copyObject(
                        sourceLocation.bucketName(), objectKey, targetBucket, objectKey)) {
                    failed++;
                    continue;
                }
                int updated = baseMapper.update(
                        null,
                        Wrappers.lambdaUpdate(WorkflowArtifact.class)
                                .eq(WorkflowArtifact::getId, artifact.getId())
                                .and(bucket -> bucket.isNull(WorkflowArtifact::getBucketName)
                                        .or()
                                        .eq(WorkflowArtifact::getBucketName, ""))
                                .eq(WorkflowArtifact::getWorkflowId, artifact.getWorkflowId())
                                .eq(WorkflowArtifact::getObjectKey, objectKey)
                                .set(WorkflowArtifact::getBucketName, targetBucket)
                                .set(WorkflowArtifact::getUpdateTime, LocalDateTime.now()));
                if (updated != 1) {
                    failed++;
                    log.warn(
                            "Skipped legacy workflow artifact source deletion after concurrent database update, artifactId={}",
                            artifact.getId());
                    continue;
                }
                if (!s3ClientUtil.removeObject(sourceLocation.bucketName(), objectKey)) {
                    failed++;
                    log.error(
                            "Migrated workflow artifact but could not remove the public source, artifactId={}",
                            artifact.getId());
                    continue;
                }
                migrated++;
            }
        }
        return new LegacyReferenceMigrationResult(migrated, failed);
    }

    private LegacyPublicCleanupResult cleanupLegacyPublicArtifacts() {
        String sourceBucket = s3ClientUtil.getDefaultBucket();
        int cleaned = 0;
        int retained = 0;
        int failed = 0;
        try {
            List<String> publicObjectKeys =
                    s3ClientUtil.listObjectKeys(
                            sourceBucket,
                            LEGACY_PUBLIC_ARTIFACT_PREFIX,
                            legacyPublicCleanupCursor,
                            LEGACY_PUBLIC_CLEANUP_BATCH_SIZE);
            if (publicObjectKeys.isEmpty()) {
                legacyPublicCleanupCursor = null;
            }
            Set<String> legacyReferencedKeys = new HashSet<>();
            if (!publicObjectKeys.isEmpty()) {
                List<WorkflowArtifact> references = baseMapper.selectList(
                        Wrappers.lambdaQuery(WorkflowArtifact.class)
                                .and(bucket -> bucket.isNull(WorkflowArtifact::getBucketName)
                                        .or()
                                        .eq(WorkflowArtifact::getBucketName, ""))
                                .in(WorkflowArtifact::getObjectKey, publicObjectKeys));
                if (references != null) {
                    references.stream()
                            .map(WorkflowArtifact::getObjectKey)
                            .filter(StringUtils::isNotBlank)
                            .forEach(legacyReferencedKeys::add);
                }
            }
            for (String objectKey : publicObjectKeys) {
                if (!StringUtils.startsWith(objectKey, LEGACY_PUBLIC_ARTIFACT_PREFIX)) {
                    retained++;
                    log.error(
                            "Refused to clean an object outside the workflow artifact prefix, objectKeyBytes={}",
                            objectKey == null ? 0 : objectKey.length());
                    continue;
                }
                if (legacyReferencedKeys.contains(objectKey)) {
                    retained++;
                    continue;
                }
                if (s3ClientUtil.removeObject(sourceBucket, objectKey)) {
                    cleaned++;
                } else {
                    retained++;
                }
            }
            if (!publicObjectKeys.isEmpty()) {
                legacyPublicCleanupCursor = publicObjectKeys.size() < LEGACY_PUBLIC_CLEANUP_BATCH_SIZE
                        ? null
                        : publicObjectKeys.getLast();
            }
        } catch (RuntimeException exception) {
            failed++;
            log.error(
                    "Unable to reconcile the legacy public workflow artifact prefix; its anonymous access remains blocked",
                    exception);
        }
        return new LegacyPublicCleanupResult(cleaned, retained, failed);
    }

    private record LegacyReferenceMigrationResult(int migrated, int failed) {}

    private record LegacyPublicCleanupResult(int cleaned, int retained, int failed) {}

    public List<WorkflowArtifactDto> listArtifacts(Long workflowId) {
        assertWorkflowVisible(workflowId);
        return list(scopeQuery(workflowId)
                .orderByDesc(WorkflowArtifact::getCreateTime)
                .orderByDesc(WorkflowArtifact::getId)
                .last("limit " + ARTIFACT_LIST_RESULT_LIMIT))
                .stream()
                .map(artifact -> toDto(artifact, false))
                .toList();
    }

    public WorkflowArtifactDto getDownloadInfo(Long artifactId) {
        WorkflowArtifact artifact = getScopedArtifact(artifactId);
        assertWorkflowVisible(artifact.getWorkflowId());
        return toDto(artifact, true);
    }

    @Transactional
    public void deleteArtifact(Long artifactId) {
        WorkflowArtifact artifact = getScopedArtifact(artifactId);
        assertWorkflowWritable(artifact.getWorkflowId());
        requireArtifactObjectLocation(artifact);
        artifact.setDeleted(Boolean.TRUE);
        artifact.setUpdateTime(LocalDateTime.now());
        if (!updateById(artifact)) {
            throw new BusinessException(ResponseEnum.INTERNAL_SERVER_ERROR);
        }
        Runnable purge = () -> purgeDeletedArtifactObject(artifact);
        if (TransactionSynchronizationManager.isSynchronizationActive()) {
            TransactionSynchronizationManager.registerSynchronization(
                    new TransactionSynchronization() {
                        @Override
                        public void afterCommit() {
                            purge.run();
                        }
                    });
        } else {
            purge.run();
        }
    }

    @Transactional
    public WorkflowArtifactDto uploadInternal(
            String token,
            Long workflowId,
            String flowId,
            String uid,
            Long spaceId,
            String runId,
            String nodeId,
            String skillId,
            String source,
            MultipartFile file) {
        validateInternalToken(token);
        String normalizedUid = validateUid(uid);
        Workflow workflow = resolveWorkflow(workflowId, flowId, normalizedUid, spaceId);
        assertArtifactScope(workflow, normalizedUid, spaceId);
        validateMetadata(runId, nodeId, skillId);
        ValidatedArtifact validatedArtifact = artifactFileValidator.validate(file);
        workflow = lockWorkflowForArtifactQuota(workflow.getId(), flowId, normalizedUid, spaceId);
        enforceWorkflowArtifactQuota(workflow.getId(), file.getSize());
        String objectKey = buildObjectKey(workflow.getId(), runId, validatedArtifact.fileName());
        try (InputStream input = file.getInputStream()) {
            s3ClientUtil.uploadObject(
                    artifactProperties.getArtifactBucket(),
                    objectKey,
                    validatedArtifact.contentType(),
                    input,
                    file.getSize(),
                    -1);
        } catch (Exception ex) {
            log.error(
                    "Upload workflow artifact failed, workflowId={}, uid={}, fileName={}",
                    workflow.getId(),
                    normalizedUid,
                    validatedArtifact.fileName(),
                    ex);
            throw new BusinessException(ResponseEnum.S3_UPLOAD_ERROR);
        }
        try {
            WorkflowArtifact artifact = new WorkflowArtifact();
            LocalDateTime now = LocalDateTime.now();
            artifact.setUid(normalizedUid);
            artifact.setSpaceId(workflow.getSpaceId());
            artifact.setWorkflowId(workflow.getId());
            artifact.setRunId(StringUtils.trimToEmpty(runId));
            artifact.setNodeId(StringUtils.trimToEmpty(nodeId));
            artifact.setSkillId(StringUtils.trimToEmpty(skillId));
            artifact.setFileName(validatedArtifact.fileName());
            artifact.setObjectKey(objectKey);
            artifact.setBucketName(artifactProperties.getArtifactBucket());
            artifact.setContentType(validatedArtifact.contentType());
            artifact.setFileSize(file.getSize());
            artifact.setSource(normalizeSource(source));
            artifact.setDeleted(Boolean.FALSE);
            artifact.setCreateTime(now);
            artifact.setUpdateTime(now);
            if (!save(artifact)) {
                throw new BusinessException(ResponseEnum.INTERNAL_SERVER_ERROR);
            }
            WorkflowArtifactDto dto = toDto(artifact, false);
            log.info(
                    "Stored workflow artifact, workflowId={}, uid={}, spaceId={}, objectKey={}, size={}, type={}",
                    workflow.getId(),
                    normalizedUid,
                    workflow.getSpaceId(),
                    objectKey,
                    file.getSize(),
                    validatedArtifact.contentType());
            return dto;
        } catch (RuntimeException exception) {
            boolean removed = s3ClientUtil.removeObject(
                    artifactProperties.getArtifactBucket(), objectKey);
            if (!removed) {
                log.error(
                        "Failed to compensate workflow artifact upload, bucket={}, objectKey={}",
                        artifactProperties.getArtifactBucket(),
                        objectKey);
            }
            throw exception;
        }
    }

    private WorkflowArtifact getScopedArtifact(Long artifactId) {
        if (artifactId == null) {
            throw new BusinessException(ResponseEnum.PARAM_ERROR);
        }
        LambdaQueryWrapper<WorkflowArtifact> wrapper = Wrappers.lambdaQuery(WorkflowArtifact.class)
                .eq(WorkflowArtifact::getId, artifactId)
                .eq(WorkflowArtifact::getDeleted, Boolean.FALSE);
        applyCurrentArtifactScope(wrapper);
        WorkflowArtifact artifact = getOne(wrapper, false);
        if (artifact == null) {
            throw new BusinessException(ResponseEnum.DATA_NOT_EXIST);
        }
        return artifact;
    }

    private LambdaQueryWrapper<WorkflowArtifact> scopeQuery(Long workflowId) {
        LambdaQueryWrapper<WorkflowArtifact> wrapper = Wrappers.lambdaQuery(WorkflowArtifact.class)
                .eq(WorkflowArtifact::getWorkflowId, workflowId)
                .eq(WorkflowArtifact::getDeleted, Boolean.FALSE);
        applyCurrentArtifactScope(wrapper);
        return wrapper;
    }

    private void applyCurrentArtifactScope(LambdaQueryWrapper<WorkflowArtifact> wrapper) {
        String currentUid = UserInfoManagerHandler.getUserId();
        Long spaceId = SpaceInfoUtil.getSpaceId();
        if (StringUtils.isBlank(currentUid)) {
            throw new BusinessException(ResponseEnum.UNAUTHORIZED);
        }
        if (spaceId != null) {
            if (spaceUserService.getRole(spaceId, currentUid) == null) {
                throw new BusinessException(ResponseEnum.INSUFFICIENT_PERMISSIONS);
            }
            wrapper.eq(WorkflowArtifact::getSpaceId, spaceId);
        } else {
            wrapper.isNull(WorkflowArtifact::getSpaceId)
                    .eq(WorkflowArtifact::getUid, currentUid);
        }
    }

    private void assertWorkflowVisible(Long workflowId) {
        Workflow workflow = workflowMapper.selectOne(Wrappers.lambdaQuery(Workflow.class)
                .eq(Workflow::getId, workflowId)
                .eq(Workflow::getDeleted, Boolean.FALSE)
                .last("limit 1"));
        if (workflow == null) {
            throw new BusinessException(ResponseEnum.WORKFLOW_NOT_EXIST);
        }
        dataPermissionCheckTool.checkWorkflowVisible(workflow, SpaceInfoUtil.getSpaceId());
    }

    private void assertWorkflowWritable(Long workflowId) {
        Workflow workflow = workflowMapper.selectOne(Wrappers.lambdaQuery(Workflow.class)
                .eq(Workflow::getId, workflowId)
                .eq(Workflow::getDeleted, Boolean.FALSE)
                .last("limit 1"));
        if (workflow == null) {
            throw new BusinessException(ResponseEnum.WORKFLOW_NOT_EXIST);
        }
        assertArtifactScope(
                workflow,
                UserInfoManagerHandler.getUserId(),
                SpaceInfoUtil.getSpaceId());
    }

    private Workflow lockWorkflowForArtifactQuota(
            Long workflowId, String requestedFlowId, String uid, Long requestedSpaceId) {
        Workflow lockedWorkflow =
                workflowMapper.selectActiveByIdForArtifactQuotaLock(workflowId);
        if (lockedWorkflow == null
                || (StringUtils.isNotBlank(requestedFlowId)
                        && !StringUtils.equals(requestedFlowId, lockedWorkflow.getFlowId()))) {
            throw new BusinessException(ResponseEnum.WORKFLOW_NOT_EXIST);
        }
        assertArtifactScope(lockedWorkflow, uid, requestedSpaceId);
        return lockedWorkflow;
    }

    private void enforceWorkflowArtifactQuota(Long workflowId, long incomingFileBytes) {
        long activeFileCount = baseMapper.countActiveByWorkflowId(workflowId);
        long activeTotalBytes = baseMapper.sumActiveBytesByWorkflowId(workflowId);
        long maxFileCount = artifactProperties.getArtifactMaxActiveFilesPerWorkflow();
        long maxTotalBytes =
                artifactProperties.getArtifactMaxActiveTotalSizePerWorkflow().toBytes();
        boolean fileCountExceeded = activeFileCount >= maxFileCount;
        boolean totalBytesExceeded = incomingFileBytes > maxTotalBytes
                || activeTotalBytes > maxTotalBytes - incomingFileBytes;
        if (fileCountExceeded || totalBytesExceeded) {
            log.warn(
                    "Rejected workflow artifact because its active quota would be exceeded, workflowId={}, activeFiles={}, activeBytes={}, incomingBytes={}",
                    workflowId,
                    activeFileCount,
                    activeTotalBytes,
                    incomingFileBytes);
            throw new BusinessException(ResponseEnum.WORKFLOW_ARTIFACT_QUOTA_EXCEEDED);
        }
    }

    /** Marks every artifact for a deleted workflow so the scheduled object purge can drain it. */
    @Transactional
    public int tombstoneWorkflowArtifacts(Long workflowId) {
        if (workflowId == null) {
            throw new BusinessException(ResponseEnum.PARAM_ERROR);
        }
        return baseMapper.update(
                null,
                Wrappers.lambdaUpdate(WorkflowArtifact.class)
                        .eq(WorkflowArtifact::getWorkflowId, workflowId)
                        .eq(WorkflowArtifact::getDeleted, Boolean.FALSE)
                        .set(WorkflowArtifact::getDeleted, Boolean.TRUE)
                        .set(WorkflowArtifact::getUpdateTime, LocalDateTime.now()));
    }

    @Scheduled(
            fixedDelayString = "${skill.sandbox.artifact-deletion-retry-interval-ms:300000}",
            initialDelayString = "${skill.sandbox.artifact-deletion-retry-initial-delay-ms:300000}")
    public void purgeDeletedArtifactObjects() {
        List<WorkflowArtifact> tombstones = baseMapper.selectList(
                Wrappers.lambdaQuery(WorkflowArtifact.class)
                        .eq(WorkflowArtifact::getDeleted, Boolean.TRUE)
                        .isNotNull(WorkflowArtifact::getObjectKey)
                        .ne(WorkflowArtifact::getObjectKey, "")
                        .orderByAsc(WorkflowArtifact::getId)
                        .last("limit " + ARTIFACT_PURGE_BATCH_SIZE));
        if (tombstones == null || tombstones.isEmpty()) {
            return;
        }
        for (WorkflowArtifact artifact : tombstones) {
            purgeDeletedArtifactObject(artifact);
        }
    }

    private void purgeDeletedArtifactObject(WorkflowArtifact artifact) {
        if (artifact == null || artifact.getId() == null) {
            return;
        }
        ArtifactObjectLocation location;
        try {
            location = requireArtifactObjectLocation(artifact);
        } catch (BusinessException exception) {
            log.error(
                    "Refused to purge workflow artifact because its storage pointer is outside the artifact scope, artifactId={}",
                    artifact.getId());
            return;
        }
        if (!s3ClientUtil.removeObject(location.bucketName(), location.objectKey())) {
            log.warn(
                    "Workflow artifact object deletion will be retried, artifactId={}",
                    artifact.getId());
            return;
        }
        try {
            var update = Wrappers.lambdaUpdate(WorkflowArtifact.class)
                    .eq(WorkflowArtifact::getId, artifact.getId())
                    .eq(WorkflowArtifact::getWorkflowId, artifact.getWorkflowId())
                    .eq(WorkflowArtifact::getDeleted, Boolean.TRUE)
                    .eq(WorkflowArtifact::getObjectKey, location.objectKey());
            if (StringUtils.isBlank(artifact.getBucketName())) {
                update.and(bucket -> bucket.isNull(WorkflowArtifact::getBucketName)
                        .or()
                        .eq(WorkflowArtifact::getBucketName, ""));
            } else {
                update.eq(WorkflowArtifact::getBucketName, artifact.getBucketName());
            }
            int updated = baseMapper.update(
                    null,
                    update.set(WorkflowArtifact::getObjectKey, null)
                            .set(WorkflowArtifact::getUpdateTime, LocalDateTime.now()));
            if (updated != 1) {
                log.warn(
                        "Workflow artifact object was deleted but its tombstone changed concurrently, artifactId={}",
                        artifact.getId());
            }
        } catch (RuntimeException exception) {
            // Object deletion is idempotent. Keep the durable tombstone pointer so a later sweep
            // can retry clearing it without starving the rest of this batch.
            log.error(
                    "Workflow artifact object was deleted but its tombstone could not be completed, artifactId={}",
                    artifact.getId(),
                    exception);
        }
    }

    private WorkflowArtifactDto toDto(WorkflowArtifact artifact, boolean includeDownloadUrl) {
        WorkflowArtifactDto dto = new WorkflowArtifactDto();
        dto.setId(artifact.getId());
        dto.setWorkflowId(artifact.getWorkflowId());
        dto.setRunId(artifact.getRunId());
        dto.setNodeId(artifact.getNodeId());
        dto.setSkillId(artifact.getSkillId());
        dto.setFileName(artifact.getFileName());
        dto.setContentType(artifact.getContentType());
        dto.setFileSize(artifact.getFileSize());
        dto.setSource(artifact.getSource());
        dto.setCreateTime(artifact.getCreateTime());
        if (includeDownloadUrl && StringUtils.isNotBlank(artifact.getObjectKey())) {
            ArtifactObjectLocation location = requireArtifactObjectLocation(artifact);
            dto.setDownloadUrl(s3ClientUtil.generatePresignedDownloadUrl(
                    location.bucketName(),
                    location.objectKey(),
                    artifact.getFileName(),
                    artifactProperties.getArtifactDownloadExpirySeconds()));
        }
        return dto;
    }

    private void validateInternalToken(String token) {
        if (!artifactUploadTokenProvider.matches(token)) {
            throw new BusinessException(ResponseEnum.UNAUTHORIZED);
        }
    }

    private Workflow resolveWorkflow(
            Long workflowId, String flowId, String uid, Long requestedSpaceId) {
        LambdaQueryWrapper<Workflow> wrapper = Wrappers.lambdaQuery(Workflow.class)
                .eq(Workflow::getDeleted, Boolean.FALSE);
        if (workflowId == null && StringUtils.isBlank(flowId)) {
            throw new BusinessException(ResponseEnum.PARAM_ERROR);
        }
        if (workflowId != null) {
            wrapper.eq(Workflow::getId, workflowId);
        }
        if (StringUtils.isNotBlank(flowId)) {
            wrapper.eq(Workflow::getFlowId, flowId);
        }
        if (requestedSpaceId == null) {
            wrapper.isNull(Workflow::getSpaceId).eq(Workflow::getUid, uid);
        } else {
            wrapper.eq(Workflow::getSpaceId, requestedSpaceId);
        }
        Workflow workflow = workflowMapper.selectOne(wrapper.last("limit 1"));
        if (workflow == null) {
            throw new BusinessException(ResponseEnum.WORKFLOW_NOT_EXIST);
        }
        return workflow;
    }

    private void assertArtifactScope(Workflow workflow, String uid, Long requestedSpaceId) {
        Long workflowSpaceId = workflow.getSpaceId();
        if (workflowSpaceId == null) {
            if (requestedSpaceId != null || !StringUtils.equals(workflow.getUid(), uid)) {
                throw new BusinessException(ResponseEnum.INSUFFICIENT_PERMISSIONS);
            }
            return;
        }
        if (!Objects.equals(workflowSpaceId, requestedSpaceId)
                || spaceUserService.getRole(workflowSpaceId, uid) == null) {
            throw new BusinessException(ResponseEnum.INSUFFICIENT_PERMISSIONS);
        }
    }

    private String validateUid(String uid) {
        String normalized = StringUtils.trimToEmpty(uid);
        if (StringUtils.isBlank(normalized) || normalized.length() > 128) {
            throw new BusinessException(ResponseEnum.PARAM_ERROR);
        }
        return normalized;
    }

    private void validateMetadata(String runId, String nodeId, String skillId) {
        if (StringUtils.length(runId) > 128
                || StringUtils.length(nodeId) > 128
                || StringUtils.length(skillId) > 128) {
            throw new BusinessException(ResponseEnum.PARAM_ERROR);
        }
    }

    private String buildObjectKey(Long workflowId, String runId, String fileName) {
        String runSegment = StringUtils.defaultIfBlank(normalizePathSegment(runId), "manual");
        return "workflow/artifacts/"
                + workflowId
                + "/"
                + runSegment
                + "/"
                + UUID.randomUUID().toString().replace("-", "")
                + "_"
                + fileName;
    }

    private ArtifactObjectLocation requireArtifactObjectLocation(WorkflowArtifact artifact) {
        if (artifact == null || artifact.getWorkflowId() == null) {
            throw new BusinessException(ResponseEnum.DATA_NOT_EXIST);
        }
        String objectKey = artifact.getObjectKey();
        String requiredPrefix = LEGACY_PUBLIC_ARTIFACT_PREFIX + artifact.getWorkflowId() + "/";
        if (StringUtils.isBlank(objectKey)
                || !StringUtils.startsWith(objectKey, requiredPrefix)
                || !hasCanonicalObjectKeySyntax(objectKey)) {
            throw new BusinessException(ResponseEnum.DATA_NOT_EXIST);
        }
        String recordedBucket = artifact.getBucketName();
        if (StringUtils.isBlank(recordedBucket)) {
            String legacyBucket = s3ClientUtil.getDefaultBucket();
            if (StringUtils.isBlank(legacyBucket)) {
                throw new BusinessException(ResponseEnum.DATA_NOT_EXIST);
            }
            return new ArtifactObjectLocation(legacyBucket, objectKey);
        }
        String privateBucket = StringUtils.trimToEmpty(artifactProperties.getArtifactBucket());
        if (!StringUtils.equals(recordedBucket, privateBucket)) {
            throw new BusinessException(ResponseEnum.DATA_NOT_EXIST);
        }
        return new ArtifactObjectLocation(privateBucket, objectKey);
    }

    private boolean hasCanonicalObjectKeySyntax(String objectKey) {
        if (objectKey.indexOf('\\') >= 0) {
            return false;
        }
        for (int index = 0; index < objectKey.length(); index++) {
            if (Character.isISOControl(objectKey.charAt(index))) {
                return false;
            }
        }
        for (String segment : objectKey.split("/", -1)) {
            if (StringUtils.isBlank(segment)
                    || StringUtils.equalsAny(segment, ".", "..")) {
                return false;
            }
        }
        return true;
    }

    private record ArtifactObjectLocation(String bucketName, String objectKey) {}

    private String normalizeSource(String source) {
        String normalized = StringUtils.trimToEmpty(source);
        if (StringUtils.equalsAny(normalized, "skill_sandbox", "code_sandbox")) {
            return normalized;
        }
        return "skill_sandbox";
    }

    private String normalizePathSegment(String value) {
        String normalized =
                StringUtils.defaultString(value).replaceAll("[^a-zA-Z0-9._-]", "_");
        return StringUtils.equalsAny(normalized, ".", "..") ? "_" : normalized;
    }
}
