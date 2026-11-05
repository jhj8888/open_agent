package com.iflytek.astron.console.hub.platform.run;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;
import org.junit.jupiter.api.Test;
import software.amazon.awssdk.awscore.exception.AwsErrorDetails;
import software.amazon.awssdk.services.s3.S3Client;
import software.amazon.awssdk.services.s3.model.*;

class OutputMultipartCleanupTest {
    private final S3Client client=mock(S3Client.class);
    private final OutputMultipartCleanup cleanup=new OutputMultipartCleanup(client,"outputs");
    private final String key="task-outputs/v1/"+"a".repeat(36)+"/"+"b".repeat(36)+"/"+"c".repeat(36);
    private MultipartUpload upload(String object,String id) {return MultipartUpload.builder().key(object).uploadId(id).build();}
    private ListMultipartUploadsResponse page(boolean truncated,MultipartUpload... uploads) {
        return ListMultipartUploadsResponse.builder().isTruncated(truncated).uploads(uploads).build();
    }
    private S3Exception missing(String code) {
        var builder=S3Exception.builder();
        builder.statusCode(404).awsErrorDetails(AwsErrorDetails.builder().errorCode(code).build());
        return (S3Exception)builder.build();
    }

    @Test void abortsOnlyExactKeyAndChecksParts() {
        when(client.listMultipartUploads(any(ListMultipartUploadsRequest.class)))
                .thenReturn(page(false,upload(key,"u1"),upload(key+"-other","u2")),page(false,upload(key+"-other","u2")));
        when(client.listParts(any(ListPartsRequest.class))).thenThrow(missing("NoSuchUpload"));
        cleanup.remove(key);
        verify(client).abortMultipartUpload(AbortMultipartUploadRequest.builder().bucket("outputs").key(key).uploadId("u1").build());
        verify(client,times(1)).abortMultipartUpload(any(AbortMultipartUploadRequest.class));
        verify(client,times(2)).listMultipartUploads(ListMultipartUploadsRequest.builder().bucket("outputs").prefix(key).maxUploads(20).build());
    }
    @Test void invalidKeyNeverTouchesStorage() {
        assertThrows(RunFailure.class,()->cleanup.remove("skills/other"));verifyNoInteractions(client);
    }
    @Test void missingUploadIsIdempotentButStillChecksParts() {
        when(client.listMultipartUploads(any(ListMultipartUploadsRequest.class))).thenReturn(page(false,upload(key,"u1")),page(false));
        when(client.abortMultipartUpload(any(AbortMultipartUploadRequest.class))).thenThrow(missing("NoSuchUpload"));
        when(client.listParts(any(ListPartsRequest.class))).thenThrow(missing("NoSuchUpload"));
        cleanup.remove(key);verify(client).listParts(any(ListPartsRequest.class));
    }
    @Test void other404IsNotSuccessfulCleanup() {
        when(client.listMultipartUploads(any(ListMultipartUploadsRequest.class))).thenReturn(page(false,upload(key,"u1")));
        when(client.abortMultipartUpload(any(AbortMultipartUploadRequest.class))).thenThrow(missing("NoSuchBucket"));
        assertThrows(S3Exception.class,()->cleanup.remove(key));
    }
    @Test void inFlightPartsRemainRetryable() {
        when(client.listMultipartUploads(any(ListMultipartUploadsRequest.class))).thenReturn(page(false,upload(key,"u1")));
        when(client.listParts(any(ListPartsRequest.class))).thenReturn(ListPartsResponse.builder().isTruncated(false)
                .parts(Part.builder().partNumber(1).size(5242880L).build()).build());
        assertThrows(RunFailure.class,()->cleanup.remove(key));
    }
    @Test void truncatedResultNeverClaimsComplete() {
        when(client.listMultipartUploads(any(ListMultipartUploadsRequest.class))).thenReturn(page(true),page(false));
        assertThrows(RunFailure.class,()->cleanup.remove(key));
        verify(client,times(2)).listMultipartUploads(any(ListMultipartUploadsRequest.class));
    }
    @Test void lateUploadRemainsRetryable() {
        when(client.listMultipartUploads(any(ListMultipartUploadsRequest.class))).thenReturn(page(false),page(false,upload(key,"late")));
        assertThrows(RunFailure.class,()->cleanup.remove(key));
    }
    @Test void emptyResultIsIdempotent() {
        when(client.listMultipartUploads(any(ListMultipartUploadsRequest.class))).thenReturn(page(false));
        cleanup.remove(key);cleanup.remove(key);
        verify(client,never()).abortMultipartUpload(any(AbortMultipartUploadRequest.class));
    }
}
