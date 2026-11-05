package com.iflytek.astron.console.hub.platform.run;

import io.minio.*;
import java.io.*;
import java.nio.file.*;
import java.security.*;
import java.util.HexFormat;
import java.util.concurrent.Semaphore;
import org.springframework.core.env.Environment;
import org.springframework.core.io.InputStreamResource;
import org.springframework.core.io.Resource;
import org.springframework.stereotype.Component;

/** Official S3 SDK, bounded streaming and a verified temporary download, never a public bucket URL. */
@Component
public class MinioTaskOutputStore implements TaskOutputStore {
    private final MinioClient client;
    private final String bucket;
    private final Semaphore downloads=new Semaphore(1), uploads=new Semaphore(2);
    public MinioTaskOutputStore(Environment env) {
        client=MinioClient.builder().endpoint(env.getRequiredProperty("s3.endpoint"))
                .credentials(env.getRequiredProperty("s3.accessKey"),env.getRequiredProperty("s3.secretKey")).build();
        client.setTimeout(10000,60000,60000);bucket=env.getRequiredProperty("s3.bucket");
    }
    private static void expected(long size,String sha) {
        if(size<0 || size>MAX_FILE_BYTES || sha==null || !sha.matches("[a-f0-9]{64}")) throw new RunFailure("invalid_output");
    }
    @Override public void put(String key,InputStream input,long size,String sha) {
        TaskOutputStore.checkKey(key);expected(size,sha);
        if(!uploads.tryAcquire()) throw new RunFailure("output_transfer_busy");
        try {
            var digest=MessageDigest.getInstance("SHA-256");
            var bounded=new FilterInputStream(input) {
                long remaining=size;
                @Override public int read() throws IOException { byte[] one=new byte[1];int n=read(one,0,1);return n<0?-1:one[0]&255; }
                @Override public int read(byte[] b,int off,int len) throws IOException {
                    if(len==0)return 0;if(remaining==0)return -1;
                    int n=in.read(b,off,(int)Math.min(len,remaining));
                    if(n<0)throw new EOFException();remaining-=n;if(n>0)digest.update(b,off,n);return n;
                }
            };
            client.putObject(PutObjectArgs.builder().bucket(bucket).object(key).contentType("application/octet-stream")
                    .stream(bounded,size,5*1024*1024).build());
            if(bounded.remaining!=0 || input.read()!=-1 || !sha.equals(HexFormat.of().formatHex(digest.digest())))
                throw new RunFailure("output_integrity_failed");
        } catch(RunFailure failure) { throw failure; }
        catch(Exception failure) { throw new RunFailure("output_storage_unavailable"); }
        finally { uploads.release(); }
    }
    @Override public Resource verified(String key,long size,String sha) {
        TaskOutputStore.checkKey(key);expected(size,sha);
        if(!downloads.tryAcquire())throw new RunFailure("output_transfer_busy");
        Path file=null;
        try {
            file=Files.createTempFile("oa-output-", ".download");
            var digest=MessageDigest.getInstance("SHA-256");long total=0;
            try(var input=client.getObject(GetObjectArgs.builder().bucket(bucket).object(key).build());var output=Files.newOutputStream(file)) {
                byte[] block=new byte[65536];int n;
                while((n=input.read(block))!=-1) { total+=n;if(total>size)throw new IOException();digest.update(block,0,n);output.write(block,0,n); }
            }
            if(total!=size || !sha.equals(HexFormat.of().formatHex(digest.digest())))throw new RunFailure("output_integrity_failed");
            final Path verified=file;
            return new InputStreamResource(new FilterInputStream(Files.newInputStream(file)) {
                private boolean closed;
                @Override public void close() throws IOException {
                    if(closed)return;closed=true;
                    try { super.close(); } finally { try { Files.deleteIfExists(verified); } finally { downloads.release(); } }
                }
            });
        } catch(Exception failure) {
            if(file!=null)try { Files.deleteIfExists(file); } catch(IOException ignored) {}
            downloads.release();
            if(failure instanceof RunFailure known)throw known;
            throw new RunFailure("output_storage_unavailable");
        }
    }
    @Override public void remove(String key) {
        TaskOutputStore.checkKey(key);
        try { client.removeObject(RemoveObjectArgs.builder().bucket(bucket).object(key).build()); }
        catch(Exception failure) { throw new RunFailure("output_storage_unavailable"); }
    }
}
