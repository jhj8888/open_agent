import com.iflytek.astron.console.hub.platform.run.MinioTaskOutputStore;
import com.iflytek.astron.console.hub.platform.run.RunFailure;
import io.minio.*;
import java.io.*;
import java.security.MessageDigest;
import java.util.*;
import org.springframework.core.env.*;

/** Disposable real-S3 probe; never receives production bucket credentials. */
public class OutputStoreRecoveryProbe {
    static final String BUCKET="output-recovery-fixture";
    static final String BASE="task-outputs/v1/11111111-1111-1111-1111-111111111111/22222222-2222-2222-2222-222222222222/";
    static final String KEEP=BASE+"33333333-3333-3333-3333-333333333333";
    static final String CANDIDATE=BASE+"44444444-4444-4444-4444-444444444444";
    static final byte[] CONTENT="retained output, UTF-8: 文件\n".getBytes(java.nio.charset.StandardCharsets.UTF_8);
    static String hash(byte[] data) throws Exception {return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(data));}
    static void require(boolean ok) {if(!ok)throw new IllegalStateException("fixture_assertion_failed");}
    interface Operation {void run() throws Exception;}
    static void rejected(Operation operation,String code) throws Exception {
        try {operation.run();} catch(RunFailure failure) {require(code.equals(failure.code()));return;}
        throw new IllegalStateException("fixture_expected_failure");
    }
    public static void main(String[] args) {
        try {execute(args[0]);System.out.println("OUTPUT_STORE_PROBE_OK="+args[0]);System.exit(0);}
        catch(Throwable failure) {System.err.println("OUTPUT_STORE_PROBE_FAILED="+failure.getClass().getSimpleName());System.exit(1);}
    }
    static void execute(String mode) throws Exception {
        String endpoint=System.getenv("FIXTURE_ENDPOINT"),key=System.getenv("FIXTURE_KEY"),secret=System.getenv("FIXTURE_SECRET");
        require(endpoint.equals("http://127.0.0.1:8333") && key.startsWith("fixture_"));
        var env=new StandardEnvironment();
        env.getPropertySources().addFirst(new MapPropertySource("fixture",Map.of("s3.endpoint",endpoint,
            "s3.accessKey",key,"s3.secretKey",secret,"s3.bucket",BUCKET)));
        var store=new MinioTaskOutputStore(env);
        if(mode.equals("prepare")) {
            var client=MinioClient.builder().endpoint(endpoint).credentials(key,secret).build();
            client.makeBucket(MakeBucketArgs.builder().bucket(BUCKET).build());
            store.put(KEEP,new ByteArrayInputStream(CONTENT),CONTENT.length,hash(CONTENT));
        } else if(mode.equals("outage")) {
            rejected(()->store.put(CANDIDATE,new ByteArrayInputStream(CONTENT),CONTENT.length,hash(CONTENT)),"output_storage_unavailable");
            rejected(()->store.verified(KEEP,CONTENT.length,hash(CONTENT)),"output_storage_unavailable");
            rejected(()->store.remove(CANDIDATE),"output_storage_unavailable");
            return;
        } else if(mode.equals("recover")) {
            // The preceding JVM was killed after S3 accepted these bytes but
            // before put() completed its final validation. Cleanup must remain
            // exact and idempotent, without touching the retained object.
            try(var stream=store.verified(CANDIDATE,CONTENT.length,hash(CONTENT)).getInputStream()) {require(Arrays.equals(CONTENT,stream.readAllBytes()));}
            store.remove(CANDIDATE);
            store.remove(CANDIDATE);
            // A fully stored object with a failed final hash is an unpublished
            // candidate. Remove only that exact key, preserving the retained key.
            rejected(()->store.put(CANDIDATE,new ByteArrayInputStream(CONTENT),CONTENT.length,"0".repeat(64)),"output_integrity_failed");
            store.remove(CANDIDATE);
            store.remove(CANDIDATE); // Idempotent recovery cleanup.
            rejected(()->store.verified(CANDIDATE,CONTENT.length,hash(CONTENT)),"output_storage_unavailable");
            store.put(CANDIDATE,new ByteArrayInputStream(CONTENT),CONTENT.length,hash(CONTENT));
            try(var stream=store.verified(CANDIDATE,CONTENT.length,hash(CONTENT)).getInputStream()) {require(Arrays.equals(CONTENT,stream.readAllBytes()));}
            store.remove(CANDIDATE);
        } else if(mode.equals("post-write-crash")) {
            var input=new ByteArrayInputStream(CONTENT) {
                @Override public synchronized int read() {
                    require(available()==0);
                    System.out.println("OUTPUT_STORE_PROBE_READY=post-write-crash");
                    System.out.flush();
                    try {new java.util.concurrent.CountDownLatch(1).await();}
                    catch(InterruptedException interrupted) {Thread.currentThread().interrupt();throw new IllegalStateException("fixture_interrupted");}
                    throw new IllegalStateException("fixture_unexpected_resume");
                }
            };
            store.put(CANDIDATE,input,CONTENT.length,hash(CONTENT));
            throw new IllegalStateException("fixture_crash_not_injected");
        } else if(mode.equals("candidate-present")) {
            try(var stream=store.verified(CANDIDATE,CONTENT.length,hash(CONTENT)).getInputStream()) {require(Arrays.equals(CONTENT,stream.readAllBytes()));}
        } else throw new IllegalArgumentException("unknown_fixture_mode");
        try(var stream=store.verified(KEEP,CONTENT.length,hash(CONTENT)).getInputStream()) {require(Arrays.equals(CONTENT,stream.readAllBytes()));}
    }
}
