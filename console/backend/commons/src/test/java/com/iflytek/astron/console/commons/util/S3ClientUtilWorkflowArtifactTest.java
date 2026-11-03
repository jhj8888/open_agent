package com.iflytek.astron.console.commons.util;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import ch.qos.logback.classic.Logger;
import ch.qos.logback.classic.spi.ILoggingEvent;
import ch.qos.logback.core.read.ListAppender;
import com.alibaba.fastjson2.JSON;
import com.alibaba.fastjson2.JSONArray;
import com.alibaba.fastjson2.JSONObject;
import com.iflytek.astron.console.commons.exception.BusinessException;
import io.minio.CopyObjectArgs;
import io.minio.GetPresignedObjectUrlArgs;
import io.minio.ListObjectsArgs;
import io.minio.MinioClient;
import io.minio.RemoveObjectArgs;
import io.minio.SetBucketPolicyArgs;
import io.minio.http.Method;
import java.io.IOException;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.slf4j.LoggerFactory;
import org.springframework.test.util.ReflectionTestUtils;

@ExtendWith(MockitoExtension.class)
class S3ClientUtilWorkflowArtifactTest {

    @Mock
    private MinioClient minioClient;

    @Mock
    private MinioClient presignClient;

    private S3ClientUtil s3ClientUtil;

    @BeforeEach
    void setUp() {
        s3ClientUtil = new S3ClientUtil();
        ReflectionTestUtils.setField(s3ClientUtil, "minioClient", minioClient);
        ReflectionTestUtils.setField(s3ClientUtil, "presignClient", presignClient);
        ReflectionTestUtils.setField(s3ClientUtil, "presignExpirySeconds", 600);
    }

    @Test
    void downloadUrlForcesAttachmentOctetStreamAndNoStore() throws Exception {
        when(presignClient.getPresignedObjectUrl(any())).thenReturn("https://download.example");

        String result = s3ClientUtil.generatePresignedDownloadUrl(
                "workflow-artifacts", "workflow/artifacts/1/file.html", "报告.html");

        ArgumentCaptor<GetPresignedObjectUrlArgs> arguments =
                ArgumentCaptor.forClass(GetPresignedObjectUrlArgs.class);
        verify(presignClient).getPresignedObjectUrl(arguments.capture());
        GetPresignedObjectUrlArgs value = arguments.getValue();
        assertThat(value.method()).isEqualTo(Method.GET);
        assertThat(value.bucket()).isEqualTo("workflow-artifacts");
        assertThat(value.object()).isEqualTo("workflow/artifacts/1/file.html");
        assertThat(value.expiry()).isEqualTo(600);
        assertThat(value.extraQueryParams().get("response-content-type"))
                .containsExactly("application/octet-stream");
        assertThat(value.extraQueryParams().get("response-cache-control"))
                .containsExactly("no-store");
        assertThat(value.extraQueryParams().get("response-content-disposition"))
                .singleElement()
                .asString()
                .contains("attachment")
                .contains("filename");
        assertThat(result).isEqualTo("https://download.example");
    }

    @Test
    void presignFailureLogsOnlyExceptionType() throws Exception {
        Logger logger = (Logger) LoggerFactory.getLogger(S3ClientUtil.class);
        ListAppender<ILoggingEvent> appender = new ListAppender<>();
        appender.start();
        logger.addAppender(appender);
        String sensitiveMessage =
                "AKIA_TEST_ONLY secret=test-secret "
                        + "https://objects.example/artifact?X-Amz-Credential=AKIA_TEST_ONLY"
                        + "&X-Amz-Signature=test-signature";

        try {
            when(presignClient.getPresignedObjectUrl(any()))
                    .thenThrow(new IOException(sensitiveMessage));

            assertThatThrownBy(() -> s3ClientUtil.generatePresignedDownloadUrl(
                    "workflow-artifacts",
                    "workflow/artifacts/1/file.html",
                    "report.html"))
                    .isInstanceOf(BusinessException.class);

            assertThat(appender.list)
                    .singleElement()
                    .satisfies(event -> {
                        assertThat(event.getFormattedMessage())
                                .contains("errorType=IOException")
                                .doesNotContain(
                                        sensitiveMessage,
                                        "AKIA_TEST_ONLY",
                                        "test-secret",
                                        "X-Amz-Signature");
                        assertThat(event.getThrowableProxy()).isNull();
                    });
        } finally {
            logger.detachAppender(appender);
            appender.stop();
        }
    }

    @Test
    void blankAndLegacyPlaceholderCredentialsFailClosed() {
        ReflectionTestUtils.setField(s3ClientUtil, "endpoint", "http://minio:9000");
        ReflectionTestUtils.setField(s3ClientUtil, "remoteEndpoint", "https://objects.example");
        ReflectionTestUtils.setField(s3ClientUtil, "defaultBucket", "console-oss");
        ReflectionTestUtils.setField(s3ClientUtil, "presignExpirySeconds", 600);

        for (String accessKey : java.util.List.of("", "astron-uploader")) {
            ReflectionTestUtils.setField(s3ClientUtil, "accessKey", accessKey);
            ReflectionTestUtils.setField(s3ClientUtil, "secretKey", "real-secret");
            assertThatThrownBy(() -> ReflectionTestUtils.invokeMethod(
                    s3ClientUtil, "validateConfiguration"))
                    .isInstanceOf(BusinessException.class);
        }
        for (String secretKey : java.util.List.of("", "astron-uploader-secret")) {
            ReflectionTestUtils.setField(s3ClientUtil, "accessKey", "real-access");
            ReflectionTestUtils.setField(s3ClientUtil, "secretKey", secretKey);
            assertThatThrownBy(() -> ReflectionTestUtils.invokeMethod(
                    s3ClientUtil, "validateConfiguration"))
                    .isInstanceOf(BusinessException.class);
        }
    }

    @Test
    void existingArtifactBucketRemovesAnonymousAllowButPreservesSecurityDeny()
            throws Exception {
        when(minioClient.bucketExists(any())).thenReturn(true);
        when(minioClient.getBucketPolicy(any())).thenReturn("""
                {
                  "Version":"2012-10-17",
                  "Statement":[
                    {
                      "Sid":"LegacyPublicRead",
                      "Effect":"Allow",
                      "Principal":"*",
                      "Action":"s3:GetObject",
                      "Resource":"arn:aws:s3:::workflow-artifacts/*"
                    },
                    {
                      "Sid":"RequireTLS",
                      "Effect":"Deny",
                      "Principal":"*",
                      "Action":"s3:*",
                      "Resource":"arn:aws:s3:::workflow-artifacts/*",
                      "Condition":{"Bool":{"aws:SecureTransport":"false"}}
                    }
                  ]
                }
                """);

        s3ClientUtil.ensurePrivateBucket("workflow-artifacts");

        ArgumentCaptor<SetBucketPolicyArgs> arguments =
                ArgumentCaptor.forClass(SetBucketPolicyArgs.class);
        verify(minioClient).setBucketPolicy(arguments.capture());
        assertThat(arguments.getValue().config())
                .contains("RequireTLS")
                .contains("aws:SecureTransport")
                .doesNotContain("LegacyPublicRead");
        verify(minioClient, never()).deleteBucketPolicy(any());
    }

    @Test
    void privateBucketRemovesNotPrincipalAndUnknownPrincipalAllows() throws Exception {
        when(minioClient.bucketExists(any())).thenReturn(true);
        when(minioClient.getBucketPolicy(any())).thenReturn("""
                {
                  "Version":"2012-10-17",
                  "Statement":[
                    {
                      "Sid":"ExceptOneRole",
                      "Effect":"Allow",
                      "NotPrincipal":{"AWS":"arn:aws:iam::123456789012:role/excluded"},
                      "Action":"s3:GetObject",
                      "Resource":"arn:aws:s3:::workflow-artifacts/*"
                    },
                    {
                      "Sid":"UnknownPrincipalShape",
                      "Effect":"Allow",
                      "Principal":{"Unexpected":"value"},
                      "Action":"s3:GetObject",
                      "Resource":"arn:aws:s3:::workflow-artifacts/*"
                    },
                    {
                      "Sid":"NamedReader",
                      "Effect":"Allow",
                      "Principal":{"AWS":"arn:aws:iam::123456789012:role/reader"},
                      "Action":"s3:GetObject",
                      "Resource":"arn:aws:s3:::workflow-artifacts/*"
                    }
                  ]
                }
                """);

        s3ClientUtil.ensurePrivateBucket("workflow-artifacts");

        ArgumentCaptor<SetBucketPolicyArgs> arguments =
                ArgumentCaptor.forClass(SetBucketPolicyArgs.class);
        verify(minioClient).setBucketPolicy(arguments.capture());
        assertThat(arguments.getValue().config())
                .doesNotContain("ExceptOneRole", "UnknownPrincipalShape")
                .contains("NamedReader");
    }

    @Test
    void publicReadPolicyPermanentlyExcludesWorkflowArtifactPrefix() throws Exception {
        ReflectionTestUtils.setField(s3ClientUtil, "defaultBucket", "console-oss");
        ReflectionTestUtils.setField(s3ClientUtil, "enablePublicRead", true);

        s3ClientUtil.restoreDefaultBucketPublicRead();

        ArgumentCaptor<SetBucketPolicyArgs> arguments =
                ArgumentCaptor.forClass(SetBucketPolicyArgs.class);
        verify(minioClient).setBucketPolicy(arguments.capture());
        JSONObject policy = JSON.parseObject(arguments.getValue().config());
        JSONObject publicRead = statementBySid(
                policy.getJSONArray("Statement"),
                "AstronPublicReadExcludingWorkflowArtifacts");
        assertThat(publicRead.containsKey("NotResource")).isTrue();
        assertThat(publicRead.getJSONArray("NotResource"))
                .contains(
                        "arn:aws:s3:::console-oss/workflow/artifacts",
                        "arn:aws:s3:::console-oss/workflow/artifacts/*");
        assertThat(publicRead.containsKey("Resource")).isFalse();

        JSONObject artifactDeny = statementBySid(
                policy.getJSONArray("Statement"), "AstronDenyWorkflowArtifactReads");
        assertThat(artifactDeny.getString("Effect")).isEqualTo("Deny");
        assertThat(artifactDeny.getJSONObject("Condition").getJSONObject("StringEquals"))
                .containsEntry("aws:principaltype", "Anonymous")
                .doesNotContainKey("aws:PrincipalType");
    }

    @Test
    void publicPolicyReplacesBroadAnonymousReadAndPreservesOnlyProvablySafeStatements()
            throws Exception {
        ReflectionTestUtils.setField(s3ClientUtil, "defaultBucket", "console-oss");
        ReflectionTestUtils.setField(s3ClientUtil, "enablePublicRead", true);
        when(minioClient.getBucketPolicy(any())).thenReturn("""
                {
                  "Version":"2012-10-17",
                  "Statement":[
                    {
                      "Sid":"CorporatePublicRead",
                      "Effect":"Allow",
                      "Principal":"*",
                      "Action":"s3:*",
                      "Resource":"arn:aws:s3:::console-oss/*",
                      "Condition":{"IpAddress":{"aws:SourceIp":"192.0.2.0/24"}}
                    },
                    {
                      "Sid":"PublicImagesOnly",
                      "Effect":"Allow",
                      "Principal":{"AWS":"*"},
                      "Action":"s3:GetObject",
                      "Resource":"arn:aws:s3:::console-oss/public/images/*"
                    },
                    {
                      "Sid":"AuthenticatedArtifactReader",
                      "Effect":"Allow",
                      "Principal":{"AWS":"arn:aws:iam::123456789012:role/reader"},
                      "Action":"s3:GetObject",
                      "Resource":"arn:aws:s3:::console-oss/workflow/artifacts/*"
                    },
                    {
                      "Sid":"RequireTLS",
                      "Effect":"Deny",
                      "Principal":"*",
                      "Action":"s3:*",
                      "Resource":"arn:aws:s3:::console-oss/*",
                      "Condition":{"Bool":{"aws:SecureTransport":"false"}}
                    }
                  ]
                }
                """);

        s3ClientUtil.restoreDefaultBucketPublicRead();

        ArgumentCaptor<SetBucketPolicyArgs> arguments =
                ArgumentCaptor.forClass(SetBucketPolicyArgs.class);
        verify(minioClient).setBucketPolicy(arguments.capture());
        assertThat(arguments.getValue().config())
                .doesNotContain("CorporatePublicRead", "aws:SourceIp")
                .contains("PublicImagesOnly")
                .contains("public/images/*")
                .contains("AuthenticatedArtifactReader")
                .contains("RequireTLS")
                .contains("aws:SecureTransport")
                .contains("AstronDenyWorkflowArtifactReads")
                .contains("AstronPublicReadExcludingWorkflowArtifacts")
                .contains("NotResource");
    }

    @Test
    void unreadablePublicPolicyFailsClosedWithoutOverwritingIt() throws Exception {
        ReflectionTestUtils.setField(s3ClientUtil, "defaultBucket", "console-oss");
        ReflectionTestUtils.setField(s3ClientUtil, "enablePublicRead", true);
        when(minioClient.getBucketPolicy(any())).thenReturn("{unreadable-policy");

        assertThatThrownBy(s3ClientUtil::restoreDefaultBucketPublicRead)
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("Refusing to replace an unreadable S3 bucket policy");

        verify(minioClient, never()).setBucketPolicy(any());
    }

    @Test
    void sharedBucketReplacesBroadNotPrincipalReadWithManagedPrefixExclusion()
            throws Exception {
        ReflectionTestUtils.setField(s3ClientUtil, "defaultBucket", "console-oss");
        ReflectionTestUtils.setField(s3ClientUtil, "enablePublicRead", true);
        when(minioClient.getBucketPolicy(any())).thenReturn("""
                {
                  "Version":"2012-10-17",
                  "Statement":[{
                    "Sid":"LegacyNotPrincipalRead",
                    "Effect":"Allow",
                    "NotPrincipal":{"AWS":"arn:aws:iam::123456789012:role/excluded"},
                    "Action":"s3:Get*",
                    "Resource":"arn:aws:s3:::console-oss/*"
                  }]
                }
                """);

        s3ClientUtil.restoreDefaultBucketPublicRead();

        ArgumentCaptor<SetBucketPolicyArgs> arguments =
                ArgumentCaptor.forClass(SetBucketPolicyArgs.class);
        verify(minioClient).setBucketPolicy(arguments.capture());
        assertThat(arguments.getValue().config())
                .doesNotContain("LegacyNotPrincipalRead")
                .contains("AstronPublicReadExcludingWorkflowArtifacts")
                .contains("NotResource");
    }

    @Test
    void objectListingUsesBoundedResumeCursor() {
        when(minioClient.listObjects(any())).thenReturn(java.util.List.of());

        assertThat(s3ClientUtil.listObjectKeys(
                        "console-oss", "workflow/artifacts/", "workflow/artifacts/7", 100))
                .isEmpty();

        ArgumentCaptor<ListObjectsArgs> arguments =
                ArgumentCaptor.forClass(ListObjectsArgs.class);
        verify(minioClient).listObjects(arguments.capture());
        assertThat(arguments.getValue().bucket()).isEqualTo("console-oss");
        assertThat(arguments.getValue().prefix()).isEqualTo("workflow/artifacts/");
        assertThat(arguments.getValue().startAfter()).isEqualTo("workflow/artifacts/7");
        assertThat(arguments.getValue().maxKeys()).isEqualTo(100);
    }

    @Test
    void copiesThenRemovesLegacyObjectUsingExplicitBuckets() throws Exception {
        assertThat(s3ClientUtil.copyObject(
                "console-oss",
                "workflow/artifacts/1/file.txt",
                "workflow-artifacts",
                "workflow/artifacts/1/file.txt"))
                .isTrue();
        assertThat(s3ClientUtil.removeObject(
                "console-oss", "workflow/artifacts/1/file.txt"))
                .isTrue();

        ArgumentCaptor<CopyObjectArgs> copy = ArgumentCaptor.forClass(CopyObjectArgs.class);
        verify(minioClient).copyObject(copy.capture());
        assertThat(copy.getValue().bucket()).isEqualTo("workflow-artifacts");
        assertThat(copy.getValue().object()).isEqualTo("workflow/artifacts/1/file.txt");
        assertThat(copy.getValue().source().bucket()).isEqualTo("console-oss");
        assertThat(copy.getValue().source().object())
                .isEqualTo("workflow/artifacts/1/file.txt");

        ArgumentCaptor<RemoveObjectArgs> remove = ArgumentCaptor.forClass(RemoveObjectArgs.class);
        verify(minioClient).removeObject(remove.capture());
        assertThat(remove.getValue().bucket()).isEqualTo("console-oss");
        assertThat(remove.getValue().object()).isEqualTo("workflow/artifacts/1/file.txt");
    }

    private static JSONObject statementBySid(JSONArray statements, String sid) {
        for (Object value : statements) {
            if (value instanceof JSONObject statement
                    && sid.equals(statement.getString("Sid"))) {
                return statement;
            }
        }
        throw new AssertionError("Missing bucket policy statement: " + sid);
    }
}
