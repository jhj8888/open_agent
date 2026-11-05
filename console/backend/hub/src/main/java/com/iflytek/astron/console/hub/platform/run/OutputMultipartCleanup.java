package com.iflytek.astron.console.hub.platform.run;

import java.net.URI;
import java.time.Duration;
import org.springframework.core.env.Environment;
import software.amazon.awssdk.auth.credentials.AwsBasicCredentials;
import software.amazon.awssdk.auth.credentials.StaticCredentialsProvider;
import software.amazon.awssdk.regions.Region;
import software.amazon.awssdk.services.s3.S3Client;
import software.amazon.awssdk.services.s3.model.*;

/** Only called after the ledger has fenced an unpublished, exact output key. */
final class OutputMultipartCleanup implements AutoCloseable {
    private final S3Client client;
    private final String bucket;

    OutputMultipartCleanup(Environment env) {
        this(S3Client.builder().endpointOverride(URI.create(env.getRequiredProperty("s3.endpoint")))
                .region(Region.of(env.getProperty("s3.region", "us-east-1"))).forcePathStyle(true)
                .credentialsProvider(StaticCredentialsProvider.create(AwsBasicCredentials.create(
                        env.getRequiredProperty("s3.accessKey"), env.getRequiredProperty("s3.secretKey"))))
                .overrideConfiguration(c -> c.apiCallTimeout(Duration.ofSeconds(15))
                        .apiCallAttemptTimeout(Duration.ofSeconds(5))).build(), env.getRequiredProperty("s3.bucket"));
    }

    OutputMultipartCleanup(S3Client client, String bucket) { this.client=client; this.bucket=bucket; }

    void remove(String key) {
        TaskOutputStore.checkKey(key);
        var request=ListMultipartUploadsRequest.builder().bucket(bucket).prefix(key).maxUploads(20).build();
        var page=client.listMultipartUploads(request);
        for(var upload:page.uploads()) {
            // Prefix is a server-side optimization, never the authorization boundary.
            if(!key.equals(upload.key()))continue;
            if(upload.uploadId()==null || upload.uploadId().isBlank())throw new RunFailure("output_storage_unavailable");
            try {
                client.abortMultipartUpload(AbortMultipartUploadRequest.builder()
                        .bucket(bucket).key(key).uploadId(upload.uploadId()).build());
            } catch(S3Exception failure) { if(!missingUpload(failure))throw failure; }
            try {
                var parts=client.listParts(ListPartsRequest.builder().bucket(bucket).key(key)
                        .uploadId(upload.uploadId()).maxParts(1).build());
                if(!parts.parts().isEmpty() || !Boolean.FALSE.equals(parts.isTruncated()))
                    throw new RunFailure("output_storage_unavailable");
            } catch(S3Exception failure) { if(!missingUpload(failure))throw failure; }
        }
        // Bounded work: another scheduled attempt drains a truncated page. Never report it cleared.
        var remaining=client.listMultipartUploads(request);
        if(!Boolean.FALSE.equals(page.isTruncated()) || !Boolean.FALSE.equals(remaining.isTruncated())
                || remaining.uploads().stream().anyMatch(upload -> key.equals(upload.key())))
            throw new RunFailure("output_storage_unavailable");
    }

    private static boolean missingUpload(S3Exception failure) {
        return failure.statusCode()==404 && failure.awsErrorDetails()!=null
                && "NoSuchUpload".equals(failure.awsErrorDetails().errorCode());
    }

    @Override public void close() { client.close(); }
}
