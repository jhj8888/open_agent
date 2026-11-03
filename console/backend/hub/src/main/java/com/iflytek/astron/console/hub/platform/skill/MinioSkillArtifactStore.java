package com.iflytek.astron.console.hub.platform.skill;

import io.minio.*;
import java.io.ByteArrayInputStream;
import org.springframework.core.env.Environment;
import org.springframework.stereotype.Component;

/** Reuses the existing S3 bucket; does not change bucket policies or other services' clients. */
@Component
public class MinioSkillArtifactStore implements SkillArtifactStore,LegacySkillObjectReader {
    private final MinioClient client;
    private final String bucket;

    public MinioSkillArtifactStore(Environment env) {
        client=MinioClient.builder().endpoint(env.getRequiredProperty("s3.endpoint"))
                .credentials(env.getRequiredProperty("s3.accessKey"),env.getRequiredProperty("s3.secretKey")).build();
        client.setTimeout(10000,30000,30000);
        bucket=env.getRequiredProperty("s3.bucket");
    }
    @Override public void put(String key,byte[] content) {
        SkillArtifactStore.checkKey(key);
        if(content.length>SkillArchiveValidator.MAX_FILE_BYTES) throw new IllegalArgumentException("Artifact exceeds size limit");
        try(var input=new ByteArrayInputStream(content)) {
            client.putObject(PutObjectArgs.builder().bucket(bucket).object(key).contentType("application/octet-stream")
                    .stream(input,content.length,-1).build());
        } catch(Exception exception) { throw new ArtifactUnavailableException(); }
    }
    @Override public byte[] get(String key,int expectedSize,String expectedDigest) {
        SkillArtifactStore.checkKey(key);
        if(expectedSize<0 || expectedSize>SkillArchiveValidator.MAX_FILE_BYTES) throw new ArtifactUnavailableException();
        try(var input=client.getObject(GetObjectArgs.builder().bucket(bucket).object(key).build())) {
            byte[] bytes=SkillArchiveValidator.readBounded(input,expectedSize);
            if(bytes.length!=expectedSize || !SkillArchiveValidator.sha256(bytes).equals(expectedDigest)) throw new ArtifactUnavailableException();
            return bytes;
        } catch(Exception exception) { throw new ArtifactUnavailableException(); }
    }
    @Override public void removePrefix(String prefix) {
        SkillArtifactStore.checkPrefix(prefix);
        try {
            for(var result:client.listObjects(ListObjectsArgs.builder().bucket(bucket).prefix(prefix).recursive(true).build())) {
                String key=result.get().objectName();
                SkillArtifactStore.checkKey(key);
                if(!key.startsWith(prefix)) throw new ArtifactUnavailableException();
                client.removeObject(RemoveObjectArgs.builder().bucket(bucket).object(key).build());
            }
        } catch(Exception exception) { throw new ArtifactUnavailableException(); }
    }
    @Override public byte[] read(String scope,String key) {
        if(scope==null || !scope.matches("(?:space-[0-9]+|user-[a-zA-Z0-9._-]+)") || key==null
                || !key.startsWith("skill-files/"+scope+"/")
                || !key.substring(("skill-files/"+scope+"/").length()).matches("[a-f0-9]{32}/[a-zA-Z0-9._-]+"))
            throw new IllegalArgumentException("Invalid legacy Skill object scope");
        try(var input=client.getObject(GetObjectArgs.builder().bucket(bucket).object(key).build())) {
            return SkillArchiveValidator.readBounded(input,SkillArchiveValidator.MAX_FILE_BYTES);
        } catch(Exception exception) { throw new ArtifactUnavailableException(); }
    }
    public static final class ArtifactUnavailableException extends RuntimeException {
        public ArtifactUnavailableException() { super("Skill artifact storage is unavailable or content verification failed"); }
    }
}
