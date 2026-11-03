package com.iflytek.astron.console.toolkit.config.properties;

import jakarta.validation.constraints.AssertTrue;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotEmpty;
import jakarta.validation.constraints.NotNull;
import java.util.LinkedHashSet;
import java.util.Set;
import lombok.Getter;
import lombok.Setter;
import org.apache.commons.lang3.StringUtils;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.util.unit.DataSize;
import org.springframework.validation.annotation.Validated;

/** Security and storage settings for workflow artifacts produced by sandboxes. */
@Getter
@Setter
@Validated
@ConfigurationProperties(prefix = "skill.sandbox")
public class SkillSandboxArtifactProperties {

    private static final long MAX_CONFIGURABLE_FILE_SIZE = DataSize.ofMegabytes(100).toBytes();
    private static final long MAX_CONFIGURABLE_WORKFLOW_TOTAL_SIZE =
            DataSize.ofGigabytes(1024).toBytes();

    /** Explicit secret. Prefer a platform secret store in clustered deployments. */
    private String artifactUploadToken;

    /** Optional persistent secret file used by the single-node Docker Compose deployment. */
    private String artifactUploadTokenFile;

    @NotBlank
    private String artifactBucket = "workflow-artifacts";

    @NotNull
    private DataSize artifactMaxFileSize = DataSize.ofMegabytes(20);

    /** Maximum number of non-deleted artifact records retained by a single workflow. */
    private int artifactMaxActiveFilesPerWorkflow = 1000;

    /** Maximum aggregate size of non-deleted artifacts retained by a single workflow. */
    @NotNull
    private DataSize artifactMaxActiveTotalSizePerWorkflow = DataSize.ofGigabytes(1);

    /** Artifact download links are deliberately independent from general console presign TTLs. */
    private int artifactDownloadExpirySeconds = 300;

    @NotEmpty
    private Set<String> artifactAllowedExtensions = new LinkedHashSet<>(Set.of(
            "txt",
            "md",
            "csv",
            "json",
            "pdf",
            "png",
            "jpg",
            "jpeg",
            "gif",
            "webp",
            "doc",
            "docx",
            "xls",
            "xlsx",
            "ppt",
            "pptx",
            "zip"));

    @AssertTrue(message = "skill.sandbox artifact upload token or token file must be configured")
    public boolean isArtifactUploadTokenSourceConfigured() {
        return StringUtils.isNotBlank(artifactUploadToken)
                || StringUtils.isNotBlank(artifactUploadTokenFile);
    }

    @AssertTrue(message = "skill.sandbox artifact max file size must be between 1 byte and 100MB")
    public boolean isArtifactMaxFileSizeValid() {
        if (artifactMaxFileSize == null) {
            return false;
        }
        long bytes = artifactMaxFileSize.toBytes();
        return bytes > 0 && bytes <= MAX_CONFIGURABLE_FILE_SIZE;
    }

    @AssertTrue(
            message = "skill.sandbox artifact max active files per workflow must be between 1 and 10000")
    public boolean isArtifactMaxActiveFilesPerWorkflowValid() {
        return artifactMaxActiveFilesPerWorkflow >= 1
                && artifactMaxActiveFilesPerWorkflow <= 10_000;
    }

    @AssertTrue(
            message = "skill.sandbox artifact max active total size per workflow must be between 1 byte and 1TB")
    public boolean isArtifactMaxActiveTotalSizePerWorkflowValid() {
        if (artifactMaxActiveTotalSizePerWorkflow == null) {
            return false;
        }
        long bytes = artifactMaxActiveTotalSizePerWorkflow.toBytes();
        return bytes > 0 && bytes <= MAX_CONFIGURABLE_WORKFLOW_TOTAL_SIZE;
    }

    @AssertTrue(message = "skill.sandbox artifact download expiry must be between 60 and 300 seconds")
    public boolean isArtifactDownloadExpiryValid() {
        return artifactDownloadExpirySeconds >= 60 && artifactDownloadExpirySeconds <= 300;
    }
}
