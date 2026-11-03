package com.iflytek.astron.console.commons.util;

import com.alibaba.fastjson2.JSONArray;
import com.alibaba.fastjson2.JSON;
import com.alibaba.fastjson2.JSONObject;
import com.iflytek.astron.console.commons.constant.ResponseEnum;
import com.iflytek.astron.console.commons.exception.BusinessException;
import io.minio.BucketExistsArgs;
import io.minio.CopyObjectArgs;
import io.minio.CopySource;
import io.minio.DeleteBucketPolicyArgs;
import io.minio.GetPresignedObjectUrlArgs;
import io.minio.GetBucketPolicyArgs;
import io.minio.ListObjectsArgs;
import io.minio.MakeBucketArgs;
import io.minio.MinioClient;
import io.minio.PutObjectArgs;
import io.minio.RemoveObjectArgs;
import io.minio.Result;
import io.minio.SetBucketPolicyArgs;
import io.minio.errors.ErrorResponseException;
import io.minio.errors.BucketPolicyTooLargeException;
import io.minio.errors.InsufficientDataException;
import io.minio.errors.InternalException;
import io.minio.errors.InvalidResponseException;
import io.minio.errors.ServerException;
import io.minio.errors.XmlParserException;
import io.minio.http.Method;
import io.minio.messages.Item;
import jakarta.annotation.PostConstruct;

import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.security.InvalidKeyException;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Set;

import lombok.Getter;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.ContentDisposition;
import org.springframework.stereotype.Component;

/**
 * Concise S3 (MinIO) client utility providing upload and presign capabilities.
 */
@Slf4j
@Component
public class S3ClientUtil {
    private static final String PRIVATE_WORKFLOW_ARTIFACT_PREFIX = "workflow/artifacts";
    private static final String LEGACY_PLACEHOLDER_ACCESS_KEY = "astron-uploader";
    private static final String LEGACY_PLACEHOLDER_SECRET_KEY =
            "astron-uploader-secret";
    private static final String MANAGED_PUBLIC_READ_SID =
            "AstronPublicReadExcludingWorkflowArtifacts";
    private static final String MANAGED_ARTIFACT_DENY_SID =
            "AstronDenyWorkflowArtifactReads";

    @Value("${s3.endpoint}")
    private String endpoint;

    @Value("${s3.remoteEndpoint}")
    private String remoteEndpoint;

    @Value("${s3.accessKey}")
    private String accessKey;

    @Value("${s3.secretKey}")
    private String secretKey;

    @Getter
    @Value("${s3.bucket}")
    private String defaultBucket;

    @Getter
    @Value("${s3.presignExpirySeconds:600}")
    private int presignExpirySeconds;

    @Value("${s3.enablePublicRead:false}")
    private boolean enablePublicRead;

    private MinioClient minioClient;
    private MinioClient presignClient;

    @PostConstruct
    public void init() {
        log.info(
                "Minio config - endpoint: {}, remoteEndpoint: {}, defaultBucket: {}, presignExpirySeconds: {}, enablePublicRead: {}",
                endpoint, remoteEndpoint, defaultBucket, presignExpirySeconds, enablePublicRead);

        // Validate required configuration
        validateConfiguration();

        this.minioClient = MinioClient.builder()
                .endpoint(endpoint)
                .credentials(accessKey, secretKey)
                .build();

        // Create a separate client for presigned URLs using remoteEndpoint
        this.presignClient = MinioClient.builder()
                .endpoint(remoteEndpoint)
                .region("us-east-1") // Force region to avoid auto-discovery network call
                .credentials(accessKey, secretKey)
                .build();

        // Check if default bucket exists, create if not
        try {
            boolean found = minioClient.bucketExists(BucketExistsArgs.builder().bucket(defaultBucket).build());
            if (!found) {
                log.info("Creating S3 bucket: {}", defaultBucket);
                try {
                    minioClient.makeBucket(
                            MakeBucketArgs.builder().bucket(defaultBucket).build());
                    log.info("Created S3 bucket: {}", defaultBucket);
                } catch (ErrorResponseException exception) {
                    if (!isConcurrentBucketCreation(exception)) {
                        throw exception;
                    }
                    log.info("S3 bucket was created concurrently: {}", defaultBucket);
                }
            } else {
                log.info("S3 bucket already exists: {}", defaultBucket);
            }

            reconcileBucketPolicy(defaultBucket, enablePublicRead);
        } catch (ErrorResponseException | InsufficientDataException | InternalException | InvalidKeyException
                | InvalidResponseException | IOException | NoSuchAlgorithmException | ServerException
                | XmlParserException e) {
            log.error(
                    "Failed to check/create/configure S3 bucket '{}' (errorType={}, errorCode={})",
                    defaultBucket,
                    exceptionType(e),
                    safeS3ErrorCode(e));
            throw new BusinessException(ResponseEnum.INTERNAL_SERVER_ERROR);
        }
    }

    /**
     * Validate required configuration parameters.
     */
    private void validateConfiguration() {
        if (endpoint == null || endpoint.trim().isEmpty()) {
            log.error("S3 endpoint is not configured");
            throw new BusinessException(ResponseEnum.INTERNAL_SERVER_ERROR);
        }
        if (remoteEndpoint == null || remoteEndpoint.trim().isEmpty()) {
            log.error("S3 remoteEndpoint is not configured");
            throw new BusinessException(ResponseEnum.INTERNAL_SERVER_ERROR);
        }
        if (accessKey == null
                || accessKey.trim().isEmpty()
                || LEGACY_PLACEHOLDER_ACCESS_KEY.equals(accessKey.trim())) {
            log.error("S3 accessKey is not configured");
            throw new BusinessException(ResponseEnum.INTERNAL_SERVER_ERROR);
        }
        if (secretKey == null
                || secretKey.trim().isEmpty()
                || LEGACY_PLACEHOLDER_SECRET_KEY.equals(secretKey.trim())) {
            log.error("S3 secretKey is not configured");
            throw new BusinessException(ResponseEnum.INTERNAL_SERVER_ERROR);
        }
        if (defaultBucket == null || defaultBucket.trim().isEmpty()) {
            log.error("S3 defaultBucket is not configured");
            throw new BusinessException(ResponseEnum.INTERNAL_SERVER_ERROR);
        }
        if (presignExpirySeconds < 1 || presignExpirySeconds > 604800) {
            log.error("S3 presignExpirySeconds must be between 1 and 604800, got: {}", presignExpirySeconds);
            throw new BusinessException(ResponseEnum.INTERNAL_SERVER_ERROR);
        }
    }

    /**
     * Build public read policy JSON for a bucket using fastjson2. This allows anonymous users to
     * read/download objects from the bucket.
     *
     * @param bucketName bucket name
     * @return JSON policy string
     */
    private JSONObject buildPublicReadStatement(String bucketName) {
        JSONObject statement = new JSONObject();
        statement.put("Sid", MANAGED_PUBLIC_READ_SID);
        statement.put("Effect", "Allow");

        JSONObject principal = new JSONObject();
        principal.put("AWS", new JSONArray().fluentAdd("*"));
        statement.put("Principal", principal);

        statement.put("Action", new JSONArray().fluentAdd("s3:GetObject"));
        statement.put(
                "NotResource",
                new JSONArray()
                        .fluentAdd(String.format(
                                "arn:aws:s3:::%s/%s", bucketName, PRIVATE_WORKFLOW_ARTIFACT_PREFIX))
                        .fluentAdd(String.format(
                                "arn:aws:s3:::%s/%s/*",
                                bucketName, PRIVATE_WORKFLOW_ARTIFACT_PREFIX)));

        return statement;
    }

    private JSONObject buildArtifactReadDenyStatement(String bucketName) {
        JSONObject statement = new JSONObject();
        statement.put("Sid", MANAGED_ARTIFACT_DENY_SID);
        statement.put("Effect", "Deny");
        statement.put("Principal", "*");
        statement.put("Action", new JSONArray().fluentAdd("s3:GetObject"));
        statement.put(
                "Condition",
                new JSONObject().fluentPut(
                        "StringEquals",
                        new JSONObject().fluentPut(
                                "aws:principaltype", "Anonymous")));
        statement.put(
                "Resource",
                new JSONArray()
                        .fluentAdd(String.format(
                                "arn:aws:s3:::%s/%s",
                                bucketName,
                                PRIVATE_WORKFLOW_ARTIFACT_PREFIX))
                        .fluentAdd(String.format(
                                "arn:aws:s3:::%s/%s/*",
                                bucketName,
                                PRIVATE_WORKFLOW_ARTIFACT_PREFIX)));
        return statement;
    }

    /**
     * Restore anonymous reads for shared assets while permanently excluding workflow artifacts.
     */
    public void restoreDefaultBucketPublicRead() {
        try {
            reconcileBucketPolicy(defaultBucket, enablePublicRead);
            log.info(
                    "Reconciled prefix-safe public read policy for S3 bucket: {}, enabled={}",
                    defaultBucket,
                    enablePublicRead);
        } catch (ErrorResponseException
                | InsufficientDataException
                | InternalException
                | InvalidKeyException
                | InvalidResponseException
                | IOException
                | NoSuchAlgorithmException
                | ServerException
                | XmlParserException exception) {
            log.error(
                    "Failed to restore public read policy for S3 bucket '{}' (errorType={}, errorCode={})",
                    defaultBucket,
                    exceptionType(exception),
                    safeS3ErrorCode(exception));
            throw new BusinessException(ResponseEnum.INTERNAL_SERVER_ERROR);
        }
    }

    /** List a bounded page of object keys below a prefix using an explicit resume cursor. */
    public List<String> listObjectKeys(
            String bucketName, String prefix, String startAfter, int maxKeys) {
        if (bucketName == null
                || bucketName.trim().isEmpty()
                || prefix == null
                || prefix.trim().isEmpty()
                || maxKeys < 1
                || maxKeys > 1000) {
            throw new BusinessException(ResponseEnum.INTERNAL_SERVER_ERROR);
        }
        List<String> objectKeys = new ArrayList<>();
        try {
            ListObjectsArgs.Builder builder = ListObjectsArgs.builder()
                    .bucket(bucketName)
                    .prefix(prefix)
                    .recursive(true)
                    .maxKeys(maxKeys);
            if (startAfter != null && !startAfter.trim().isEmpty()) {
                builder.startAfter(startAfter);
            }
            Iterable<Result<Item>> results = minioClient.listObjects(builder.build());
            for (Result<Item> result : results) {
                Item item = result.get();
                if (item != null && item.objectName() != null) {
                    objectKeys.add(item.objectName());
                    if (objectKeys.size() >= maxKeys) {
                        break;
                    }
                }
            }
            return objectKeys;
        } catch (Exception exception) {
            log.error(
                    "Failed to list S3 objects in bucket '{}' below prefix '{}' (errorType={})",
                    bucketName,
                    prefix,
                    exceptionType(exception));
            throw new BusinessException(ResponseEnum.INTERNAL_SERVER_ERROR);
        }
    }

    /**
     * Ensure that a dedicated bucket exists and has no anonymous bucket policy.
     *
     * <p>
     * This method is intentionally additive: it does not change the policy of the shared default bucket
     * used by legacy public assets.
     * </p>
     *
     * @param bucketName dedicated private bucket name
     */
    public void ensurePrivateBucket(String bucketName) {
        if (bucketName == null || bucketName.trim().isEmpty()) {
            throw new BusinessException(ResponseEnum.INTERNAL_SERVER_ERROR);
        }
        try {
            boolean found = minioClient.bucketExists(
                    BucketExistsArgs.builder().bucket(bucketName).build());
            if (!found) {
                try {
                    minioClient.makeBucket(
                            MakeBucketArgs.builder().bucket(bucketName).build());
                    log.info("Created private S3 bucket: {}", bucketName);
                } catch (ErrorResponseException exception) {
                    if (!isConcurrentBucketCreation(exception)) {
                        throw exception;
                    }
                    log.info("Private S3 bucket was created concurrently: {}", bucketName);
                }
            }
            reconcileBucketPolicy(bucketName, false);
        } catch (ErrorResponseException
                | InsufficientDataException
                | InternalException
                | InvalidKeyException
                | InvalidResponseException
                | IOException
                | NoSuchAlgorithmException
                | ServerException
                | XmlParserException exception) {
            log.error(
                    "Failed to initialize private S3 bucket '{}' (errorType={}, errorCode={})",
                    bucketName,
                    exceptionType(exception),
                    safeS3ErrorCode(exception));
            throw new BusinessException(ResponseEnum.INTERNAL_SERVER_ERROR);
        }
    }

    /**
     * Preserve non-public conditions and deny statements while managing only anonymous access. An empty
     * policy is deleted only after every original statement was proven to be an anonymous Allow.
     */
    private void reconcileBucketPolicy(String bucketName, boolean publicRead)
            throws ErrorResponseException,
            InsufficientDataException,
            InternalException,
            InvalidKeyException,
            InvalidResponseException,
            IOException,
            NoSuchAlgorithmException,
            ServerException,
            XmlParserException {
        String rawPolicy = getBucketPolicyIfPresent(bucketName);
        JSONObject policy;
        JSONArray originalStatements;
        if (rawPolicy == null) {
            policy = new JSONObject();
            policy.put("Version", "2012-10-17");
            originalStatements = new JSONArray();
        } else {
            try {
                policy = JSON.parseObject(rawPolicy);
            } catch (RuntimeException exception) {
                throw new IllegalStateException(
                        "Refusing to replace an unreadable S3 bucket policy for " + bucketName,
                        exception);
            }
            originalStatements = policy.getJSONArray("Statement");
            if (originalStatements == null) {
                throw new IllegalStateException(
                        "Refusing to replace an S3 bucket policy without a Statement array for "
                                + bucketName);
            }
        }

        JSONArray retainedStatements = new JSONArray();
        boolean changed = false;
        for (Object value : originalStatements) {
            if (!(value instanceof JSONObject statement)) {
                throw new IllegalStateException(
                        "Refusing to replace an S3 bucket policy with a non-object statement for "
                                + bucketName);
            }
            String sid = statement.getString("Sid");
            if (MANAGED_PUBLIC_READ_SID.equals(sid)
                    || MANAGED_ARTIFACT_DENY_SID.equals(sid)) {
                changed = true;
                continue;
            }
            boolean anonymousAllow = "Allow".equalsIgnoreCase(statement.getString("Effect"))
                    && mayAllowAnonymousPrincipal(statement);
            if (!publicRead && anonymousAllow) {
                changed = true;
                continue;
            }
            if (publicRead
                    && anonymousAllow
                    && statementMayAllowGetObject(statement)
                    && resourceMayCoverWorkflowArtifacts(statement, bucketName)) {
                // Legacy broad public-read statements must not survive reconciliation. The
                // managed NotResource statement below is the only anonymous read grant allowed
                // to overlap the bucket namespace containing workflow artifacts.
                changed = true;
                continue;
            }
            retainedStatements.add(statement);
        }

        if (publicRead) {
            retainedStatements.add(buildPublicReadStatement(bucketName));
            retainedStatements.add(buildArtifactReadDenyStatement(bucketName));
            changed = true;
        }

        if (!changed) {
            return;
        }
        if (retainedStatements.isEmpty()) {
            deleteEmptyBucketPolicyIfPresent(bucketName);
            return;
        }
        policy.put("Statement", retainedStatements);
        if (policy.getString("Version") == null) {
            policy.put("Version", "2012-10-17");
        }
        minioClient.setBucketPolicy(SetBucketPolicyArgs.builder()
                .bucket(bucketName)
                .config(policy.toJSONString())
                .build());
    }

    private String getBucketPolicyIfPresent(String bucketName)
            throws ErrorResponseException,
            InsufficientDataException,
            InternalException,
            InvalidKeyException,
            InvalidResponseException,
            IOException,
            NoSuchAlgorithmException,
            ServerException,
            XmlParserException {
        try {
            String policy = minioClient.getBucketPolicy(
                    GetBucketPolicyArgs.builder().bucket(bucketName).build());
            return policy == null || policy.trim().isEmpty() ? null : policy;
        } catch (BucketPolicyTooLargeException exception) {
            throw new IllegalStateException(
                    "Refusing to replace an S3 bucket policy that exceeds the SDK retrieval limit for "
                            + bucketName,
                    exception);
        } catch (ErrorResponseException exception) {
            if (isMissingBucketPolicy(exception)) {
                return null;
            }
            throw exception;
        }
    }

    private void deleteEmptyBucketPolicyIfPresent(String bucketName)
            throws ErrorResponseException,
            InsufficientDataException,
            InternalException,
            InvalidKeyException,
            InvalidResponseException,
            IOException,
            NoSuchAlgorithmException,
            ServerException,
            XmlParserException {
        try {
            minioClient.deleteBucketPolicy(
                    DeleteBucketPolicyArgs.builder().bucket(bucketName).build());
            log.info("Removed an empty anonymous-only bucket policy from: {}", bucketName);
        } catch (ErrorResponseException exception) {
            if (!isMissingBucketPolicy(exception)) {
                throw exception;
            }
        }
    }

    private boolean isMissingBucketPolicy(ErrorResponseException exception) {
        String code = exception.errorResponse() == null
                ? null
                : exception.errorResponse().code();
        return "NoSuchBucketPolicy".equals(code) || "NoSuchPolicy".equals(code);
    }

    private boolean mayAllowAnonymousPrincipal(JSONObject statement) {
        if (statement.containsKey("NotPrincipal")) {
            return true;
        }
        if (!statement.containsKey("Principal")) {
            return true;
        }
        Object principal = statement.get("Principal");
        if ("*".equals(principal)) {
            return true;
        }
        if (principal instanceof String) {
            return false;
        }
        if (!(principal instanceof JSONObject principalObject)) {
            return true;
        }
        if (principalObject.isEmpty()) {
            return true;
        }
        for (String principalType : principalObject.keySet()) {
            if (!Set.of("AWS", "Service", "Federated", "CanonicalUser")
                    .contains(principalType)) {
                return true;
            }
            Object identities = principalObject.get(principalType);
            if (containsWildcard(identities)) {
                return true;
            }
            if (!(identities instanceof String) && !(identities instanceof Iterable<?>)) {
                return true;
            }
        }
        return false;
    }

    private boolean actionIncludesGetObject(Object action) {
        if (action instanceof String value) {
            return wildcardMatches(value.toLowerCase(), "s3:getobject");
        }
        if (action instanceof Iterable<?> actions) {
            for (Object value : actions) {
                if (actionIncludesGetObject(value)) {
                    return true;
                }
            }
        }
        return false;
    }

    private boolean statementMayAllowGetObject(JSONObject statement) {
        if (statement.containsKey("Action")) {
            return actionIncludesGetObject(statement.get("Action"));
        }
        if (statement.containsKey("NotAction")) {
            return !actionIncludesGetObject(statement.get("NotAction"));
        }
        // An unrecognized anonymous Allow is not safe enough to preserve in a public bucket.
        return true;
    }

    private boolean resourceMayCoverWorkflowArtifacts(
            JSONObject statement, String bucketName) {
        if (statement.containsKey("NotResource")) {
            // Only the project-owned NotResource statement is allowed, and it was removed by Sid
            // before reaching this branch. Unknown negative-resource policies are too difficult to
            // prove safe across S3-compatible implementations.
            return true;
        }
        Object resource = statement.get("Resource");
        if (resource instanceof String value) {
            return resourceMayCoverWorkflowArtifacts(value, bucketName);
        }
        if (resource instanceof Iterable<?> resources) {
            boolean recognized = false;
            for (Object value : resources) {
                if (!(value instanceof String resourceValue)) {
                    return true;
                }
                recognized = true;
                if (resourceMayCoverWorkflowArtifacts(resourceValue, bucketName)) {
                    return true;
                }
            }
            return !recognized;
        }
        return true;
    }

    private boolean resourceMayCoverWorkflowArtifacts(
            String resource, String bucketName) {
        if ("*".equals(resource)) {
            return true;
        }
        String arnPrefix = "arn:aws:s3:::";
        if (!resource.startsWith(arnPrefix)) {
            return true;
        }
        String bucketAndKey = resource.substring(arnPrefix.length());
        int keySeparator = bucketAndKey.indexOf('/');
        String bucketPattern = keySeparator < 0
                ? bucketAndKey
                : bucketAndKey.substring(0, keySeparator);
        if (!wildcardMatches(bucketPattern, bucketName)) {
            return false;
        }
        // GetObject does not apply to a bucket ARN without an object key.
        if (keySeparator < 0 || keySeparator == bucketAndKey.length() - 1) {
            return false;
        }

        String keyPattern = bucketAndKey.substring(keySeparator + 1);
        int wildcardIndex = firstWildcardIndex(keyPattern);
        if (wildcardIndex < 0) {
            return keyPattern.equals(PRIVATE_WORKFLOW_ARTIFACT_PREFIX)
                    || keyPattern.startsWith(PRIVATE_WORKFLOW_ARTIFACT_PREFIX + "/");
        }
        String literalPrefix = keyPattern.substring(0, wildcardIndex);
        return PRIVATE_WORKFLOW_ARTIFACT_PREFIX.startsWith(literalPrefix)
                || literalPrefix.equals(PRIVATE_WORKFLOW_ARTIFACT_PREFIX)
                || literalPrefix.startsWith(PRIVATE_WORKFLOW_ARTIFACT_PREFIX + "/");
    }

    private int firstWildcardIndex(String value) {
        int star = value.indexOf('*');
        int question = value.indexOf('?');
        if (star < 0) {
            return question;
        }
        if (question < 0) {
            return star;
        }
        return Math.min(star, question);
    }

    private boolean wildcardMatches(String pattern, String value) {
        int patternIndex = 0;
        int valueIndex = 0;
        int starIndex = -1;
        int valueAfterStar = -1;
        while (valueIndex < value.length()) {
            if (patternIndex < pattern.length()
                    && (pattern.charAt(patternIndex) == '?'
                            || Character.toLowerCase(pattern.charAt(patternIndex)) == Character.toLowerCase(value.charAt(valueIndex)))) {
                patternIndex++;
                valueIndex++;
            } else if (patternIndex < pattern.length()
                    && pattern.charAt(patternIndex) == '*') {
                starIndex = patternIndex++;
                valueAfterStar = valueIndex;
            } else if (starIndex >= 0) {
                patternIndex = starIndex + 1;
                valueIndex = ++valueAfterStar;
            } else {
                return false;
            }
        }
        while (patternIndex < pattern.length() && pattern.charAt(patternIndex) == '*') {
            patternIndex++;
        }
        return patternIndex == pattern.length();
    }

    private boolean containsWildcard(Object value) {
        if ("*".equals(value)) {
            return true;
        }
        if (value instanceof Iterable<?> values) {
            for (Object candidate : values) {
                if ("*".equals(candidate)) {
                    return true;
                }
            }
        }
        return false;
    }

    private boolean isConcurrentBucketCreation(ErrorResponseException exception) {
        String code = exception.errorResponse() == null
                ? null
                : exception.errorResponse().code();
        return "BucketAlreadyOwnedByYou".equals(code) || "BucketAlreadyExists".equals(code);
    }

    /**
     * Upload object (stream). Caller is responsible for closing the input stream.
     *
     * @param bucketName target bucket
     * @param objectKey object key (path)
     * @param contentType MIME type, e.g., "application/octet-stream" or a specific type
     * @param inputStream input stream
     * @param objectSize total object size (-1 if unknown, provide partSize)
     * @param partSize part size (required when objectSize=-1, recommend >= 10MB)
     * @return uploaded object URL
     */
    public String uploadObject(String bucketName, String objectKey, String contentType, InputStream inputStream,
            long objectSize, long partSize) {
        // Validate parameters
        if (bucketName == null || bucketName.trim().isEmpty()) {
            log.error("Bucket name cannot be null or empty");
            throw new BusinessException(ResponseEnum.S3_UPLOAD_ERROR);
        }
        if (objectKey == null || objectKey.trim().isEmpty()) {
            log.error("Object key cannot be null or empty");
            throw new BusinessException(ResponseEnum.S3_UPLOAD_ERROR);
        }
        if (inputStream == null) {
            log.error("Input stream cannot be null");
            throw new BusinessException(ResponseEnum.S3_UPLOAD_ERROR);
        }

        try {
            PutObjectArgs.Builder builder = PutObjectArgs.builder()
                    .bucket(bucketName)
                    .object(objectKey)
                    .stream(inputStream, objectSize, partSize);

            if (contentType != null && !contentType.isEmpty()) {
                builder.contentType(contentType);
            }

            minioClient.putObject(builder.build());

            // Build object URL
            return buildObjectUrl(bucketName, objectKey);
        } catch (ErrorResponseException | InsufficientDataException | InternalException | InvalidKeyException
                | InvalidResponseException | IOException | NoSuchAlgorithmException | ServerException
                | XmlParserException e) {
            if (log.isErrorEnabled()) {
                log.error("S3 upload failed (errorType={})", exceptionType(e));
            }
            throw new BusinessException(ResponseEnum.S3_UPLOAD_ERROR);
        }
    }

    /**
     * Build object URL.
     *
     * @param bucketName bucket name
     * @param objectKey object key
     * @return full object URL
     */
    private String buildObjectUrl(String bucketName, String objectKey) {
        // Remove trailing slash from remoteEndpoint if present
        String baseUrl = remoteEndpoint.endsWith("/") ? remoteEndpoint.substring(0, remoteEndpoint.length() - 1)
                : remoteEndpoint;
        // Remove leading slash from objectKey if present
        String normalizedObjectKey = objectKey.startsWith("/") ? objectKey.substring(1) : objectKey;
        return String.format("%s/%s/%s", baseUrl, bucketName, normalizedObjectKey);
    }

    /**
     * Upload object to default bucket (stream). Caller closes the stream.
     *
     * @param objectKey object key (path)
     * @param contentType MIME type
     * @param inputStream input stream
     * @param objectSize total object size (-1 if unknown, provide partSize)
     * @param partSize part size (required when objectSize=-1, recommend >= 10MB)
     * @return uploaded object URL
     */
    public String uploadObject(String objectKey, String contentType, InputStream inputStream, long objectSize,
            long partSize) {
        return uploadObject(defaultBucket, objectKey, contentType, inputStream, objectSize, partSize);
    }

    /**
     * Upload byte array.
     *
     * @param bucketName target bucket
     * @param objectKey object key (path)
     * @param contentType MIME type
     * @param data byte array
     * @return uploaded object URL
     */
    public String uploadObject(String bucketName, String objectKey, String contentType, byte[] data) {
        // Validate parameters
        if (data == null) {
            log.error("Data byte array cannot be null");
            throw new BusinessException(ResponseEnum.S3_UPLOAD_ERROR);
        }

        try (InputStream inputStream = new ByteArrayInputStream(data)) {
            return uploadObject(bucketName, objectKey, contentType, inputStream, data.length, -1);
        } catch (IOException e) {
            // ByteArrayInputStream.close won't throw IOException; present to satisfy
            // try-with-resources
            throw new BusinessException(ResponseEnum.S3_UPLOAD_ERROR);
        }
    }

    /**
     * Upload byte array to default bucket.
     *
     * @param objectKey object key (path)
     * @param contentType MIME type
     * @param data byte array
     * @return uploaded object URL
     */
    public String uploadObject(String objectKey, String contentType, byte[] data) {
        return uploadObject(defaultBucket, objectKey, contentType, data);
    }

    /**
     * Simplified upload with auto-detected file size. Caller closes the stream.
     *
     * @param bucketName target bucket
     * @param objectKey object key (path)
     * @param contentType MIME type
     * @param inputStream input stream
     * @return uploaded object URL
     */
    public String uploadObject(String bucketName, String objectKey, String contentType, InputStream inputStream) {
        // Use -1 as objectSize; MinIO will use multipart upload (recommend 5MB part
        // size)
        return uploadObject(bucketName, objectKey, contentType, inputStream, -1, 5L * 1024 * 1024);
    }

    /**
     * Simplified upload to default bucket; auto-detect size. Caller closes the stream.
     *
     * @param objectKey object key (path)
     * @param contentType MIME type
     * @param inputStream input stream
     * @return uploaded object URL
     */
    public String uploadObject(String objectKey, String contentType, InputStream inputStream) {
        return uploadObject(defaultBucket, objectKey, contentType, inputStream);
    }

    /**
     * Best-effort deletion used to compensate an upload when the corresponding database write fails.
     *
     * @param bucketName target bucket
     * @param objectKey object key (path)
     * @return {@code true} when MinIO accepted the delete request
     */
    public boolean removeObject(String bucketName, String objectKey) {
        if (bucketName == null
                || bucketName.trim().isEmpty()
                || objectKey == null
                || objectKey.trim().isEmpty()) {
            return false;
        }
        try {
            minioClient.removeObject(
                    RemoveObjectArgs.builder().bucket(bucketName).object(objectKey).build());
            return true;
        } catch (ErrorResponseException
                | InsufficientDataException
                | InternalException
                | InvalidKeyException
                | InvalidResponseException
                | IOException
                | NoSuchAlgorithmException
                | ServerException
                | XmlParserException exception) {
            log.error(
                    "S3 object deletion failed for bucket '{}', object '{}' (errorType={})",
                    bucketName,
                    objectKey,
                    exceptionType(exception));
            return false;
        }
    }

    /**
     * Copy an object between buckets without streaming its contents through the console service.
     *
     * @return {@code true} when MinIO completed the server-side copy
     */
    public boolean copyObject(
            String sourceBucket, String sourceObjectKey, String targetBucket, String targetObjectKey) {
        if (sourceBucket == null
                || sourceBucket.trim().isEmpty()
                || sourceObjectKey == null
                || sourceObjectKey.trim().isEmpty()
                || targetBucket == null
                || targetBucket.trim().isEmpty()
                || targetObjectKey == null
                || targetObjectKey.trim().isEmpty()) {
            return false;
        }
        try {
            minioClient.copyObject(CopyObjectArgs.builder()
                    .bucket(targetBucket)
                    .object(targetObjectKey)
                    .source(CopySource.builder()
                            .bucket(sourceBucket)
                            .object(sourceObjectKey)
                            .build())
                    .build());
            return true;
        } catch (ErrorResponseException
                | InsufficientDataException
                | InternalException
                | InvalidKeyException
                | InvalidResponseException
                | IOException
                | NoSuchAlgorithmException
                | ServerException
                | XmlParserException exception) {
            log.error(
                    "S3 object copy failed from '{}/{}' to '{}/{}' (errorType={})",
                    sourceBucket,
                    sourceObjectKey,
                    targetBucket,
                    targetObjectKey,
                    exceptionType(exception));
            return false;
        }
    }

    /**
     * Generate a presigned PUT URL for browser direct upload.
     *
     * @param bucketName target bucket
     * @param objectKey object key
     * @param expirySeconds expiry in seconds (MinIO requires 1..604800)
     * @return URL usable for HTTP PUT
     */
    public String generatePresignedPutUrl(String bucketName, String objectKey, int expirySeconds) {
        // Validate parameters
        if (bucketName == null || bucketName.trim().isEmpty()) {
            log.error("Bucket name cannot be null or empty");
            throw new BusinessException(ResponseEnum.S3_PRESIGN_ERROR);
        }
        if (objectKey == null || objectKey.trim().isEmpty()) {
            log.error("Object key cannot be null or empty");
            throw new BusinessException(ResponseEnum.S3_PRESIGN_ERROR);
        }
        if (expirySeconds < 1 || expirySeconds > 604800) {
            log.error("Expiry seconds must be between 1 and 604800, got: {}", expirySeconds);
            throw new BusinessException(ResponseEnum.S3_PRESIGN_ERROR);
        }

        try {
            return presignClient.getPresignedObjectUrl(GetPresignedObjectUrlArgs.builder()
                    .method(Method.PUT)
                    .bucket(bucketName)
                    .object(objectKey)
                    .expiry(expirySeconds)
                    .build());
        } catch (ErrorResponseException | InsufficientDataException | InternalException | InvalidKeyException
                | InvalidResponseException | IOException | NoSuchAlgorithmException | XmlParserException
                | ServerException e) {
            log.error(
                    "S3 presign PUT failed for bucket '{}', object '{}' (errorType={})",
                    bucketName,
                    objectKey,
                    exceptionType(e));
            throw new BusinessException(ResponseEnum.S3_PRESIGN_ERROR);
        }
    }

    /**
     * Generate a presigned PUT URL in the default bucket using default expiry.
     *
     * @param objectKey object key
     * @return URL usable for HTTP PUT
     */
    public String generatePresignedPutUrl(String objectKey) {
        return generatePresignedPutUrl(defaultBucket, objectKey, presignExpirySeconds);
    }

    /**
     * Generate a presigned GET URL for reading/downloading an object.
     *
     * @param bucketName target bucket
     * @param objectKey object key
     * @param expirySeconds expiry in seconds (MinIO requires 1..604800)
     * @return URL usable for HTTP GET
     */
    public String generatePresignedGetUrl(String bucketName, String objectKey, int expirySeconds) {
        // Validate parameters
        if (bucketName == null || bucketName.trim().isEmpty()) {
            log.error("Bucket name cannot be null or empty");
            throw new BusinessException(ResponseEnum.S3_PRESIGN_ERROR);
        }
        if (objectKey == null || objectKey.trim().isEmpty()) {
            log.error("Object key cannot be null or empty");
            throw new BusinessException(ResponseEnum.S3_PRESIGN_ERROR);
        }
        if (expirySeconds < 1 || expirySeconds > 604800) {
            log.error("Expiry seconds must be between 1 and 604800, got: {}", expirySeconds);
            throw new BusinessException(ResponseEnum.S3_PRESIGN_ERROR);
        }

        try {
            return presignClient.getPresignedObjectUrl(
                    GetPresignedObjectUrlArgs.builder()
                            .method(Method.GET)
                            .bucket(bucketName)
                            .object(objectKey)
                            .expiry(expirySeconds)
                            .build());
        } catch (ErrorResponseException | InsufficientDataException | InternalException | InvalidKeyException
                | InvalidResponseException | IOException | NoSuchAlgorithmException | XmlParserException
                | ServerException e) {
            log.error(
                    "S3 presign GET failed for bucket '{}', object '{}' (errorType={})",
                    bucketName,
                    objectKey,
                    exceptionType(e));
            throw new BusinessException(ResponseEnum.S3_PRESIGN_ERROR);
        }
    }

    /**
     * Generate a presigned GET URL in the default bucket using default expiry.
     *
     * @param objectKey object key
     * @return URL usable for HTTP GET
     */
    public String generatePresignedGetUrl(String objectKey) {
        return generatePresignedGetUrl(defaultBucket, objectKey, presignExpirySeconds);
    }

    /**
     * Generate a short-lived download URL that forces attachment handling and MIME sniffing defenses.
     */
    public String generatePresignedDownloadUrl(String bucketName, String objectKey, String fileName) {
        return generatePresignedDownloadUrl(
                bucketName, objectKey, fileName, presignExpirySeconds);
    }

    /**
     * Generate a forced-attachment download URL with a caller-selected short expiry.
     */
    public String generatePresignedDownloadUrl(
            String bucketName, String objectKey, String fileName, int expirySeconds) {
        if (bucketName == null
                || bucketName.trim().isEmpty()
                || objectKey == null
                || objectKey.trim().isEmpty()
                || fileName == null
                || fileName.trim().isEmpty()
                || expirySeconds < 1
                || expirySeconds > 604800) {
            throw new BusinessException(ResponseEnum.S3_PRESIGN_ERROR);
        }
        String disposition = ContentDisposition.attachment()
                .filename(fileName, StandardCharsets.UTF_8)
                .build()
                .toString();
        Map<String, String> responseHeaders = Map.of(
                "response-cache-control", "no-store",
                "response-content-disposition", disposition,
                "response-content-type", "application/octet-stream");
        try {
            return presignClient.getPresignedObjectUrl(
                    GetPresignedObjectUrlArgs.builder()
                            .method(Method.GET)
                            .bucket(bucketName)
                            .object(objectKey)
                            .expiry(expirySeconds)
                            .extraQueryParams(responseHeaders)
                            .build());
        } catch (ErrorResponseException
                | InsufficientDataException
                | InternalException
                | InvalidKeyException
                | InvalidResponseException
                | IOException
                | NoSuchAlgorithmException
                | XmlParserException
                | ServerException exception) {
            log.error(
                    "S3 presigned attachment GET failed for bucket '{}', object '{}' (errorType={})",
                    bucketName,
                    objectKey,
                    exceptionType(exception));
            throw new BusinessException(ResponseEnum.S3_PRESIGN_ERROR);
        }
    }

    private static String exceptionType(Throwable exception) {
        String simpleName = exception.getClass().getSimpleName();
        return simpleName.isEmpty() ? exception.getClass().getName() : simpleName;
    }

    private static String safeS3ErrorCode(Throwable exception) {
        if (!(exception instanceof ErrorResponseException errorResponseException)
                || errorResponseException.errorResponse() == null) {
            return "unavailable";
        }
        String code = errorResponseException.errorResponse().code();
        if (code == null || code.isEmpty() || code.length() > 64) {
            return "unavailable";
        }
        for (int index = 0; index < code.length(); index++) {
            char value = code.charAt(index);
            if (!Character.isLetterOrDigit(value)
                    && value != '.'
                    && value != '_'
                    && value != '-') {
                return "unavailable";
            }
        }
        return code;
    }
}
