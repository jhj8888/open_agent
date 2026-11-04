package com.iflytek.astron.console.hub.platform.model;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.iflytek.astron.console.commons.tenant.TenantAccessService;
import com.iflytek.astron.console.commons.tenant.TenantAccessDeniedException;
import com.iflytek.astron.console.commons.tenant.TenantPermission;
import com.iflytek.astron.console.commons.util.RequestContextUtil;
import com.iflytek.astron.console.hub.platform.model.PlatformModelController.ModelInput;
import java.time.Duration;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import org.springframework.ai.chat.prompt.Prompt;
import org.springframework.ai.openai.OpenAiChatModel;
import org.springframework.ai.openai.OpenAiChatOptions;
import org.springframework.ai.openai.api.OpenAiApi;
import org.springframework.core.env.Environment;
import org.springframework.dao.OptimisticLockingFailureException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.retry.support.RetryTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

/** Tenant API -> verified request identity -> scoped config -> trusted SDK transport. */
@Service
public class ModelConnectionService {
    private static final String PUBLIC_COLUMNS = "c.connection_id,c.display_name,c.current_version,c.enabled,v.endpoint,v.model_name,v.protocol,"
            + "v.supports_stream,v.supports_tools,v.supports_usage,v.connect_timeout_seconds,v.read_timeout_seconds,v.total_timeout_seconds,v.ca_ref,v.credential_ref,v.revoked";
    private final JdbcTemplate jdbc;
    private final TenantAccessService access;
    private final ModelCredentialCipher cipher;
    private final ModelEndpointPolicy policy;
    private final Environment environment;
    private final ObjectMapper json;

    public ModelConnectionService(JdbcTemplate jdbc, TenantAccessService access, ModelCredentialCipher cipher,
            ModelEndpointPolicy policy, Environment environment, ObjectMapper json) {
        this.jdbc=jdbc; this.access=access; this.cipher=cipher; this.policy=policy; this.environment=environment; this.json=json;
    }

    public List<Map<String,Object>> list(String tenant, String env) {
        access.require(tenant, env, TenantPermission.METADATA_READ);
        return jdbc.queryForList("SELECT " + PUBLIC_COLUMNS + " FROM oa_model_connection c JOIN oa_model_connection_version v "
                + "ON v.tenant_id=c.tenant_id AND v.environment=c.environment AND v.connection_id=c.connection_id AND v.version=c.current_version "
                + "WHERE c.tenant_id=? AND c.environment=? ORDER BY c.created_at DESC", tenant, env);
    }

    @Transactional
    public Map<String,Object> save(String tenant, String env, String existingId, ModelInput input) {
        // Tenant row lock serializes with role/status changes; recheck permission after acquiring it.
        if (jdbc.queryForList("SELECT tenant_id FROM oa_tenant WHERE tenant_id=? FOR UPDATE", tenant).isEmpty()) throw new TenantAccessDeniedException();
        access.require(tenant, env, TenantPermission.CREDENTIAL_MANAGE);
        policy.validate(input.endpoint());
        if (input.totalTimeoutSeconds() < input.readTimeoutSeconds()) throw new IllegalArgumentException("Total timeout must cover the read timeout");
        caFile(input.caRef());
        String id = existingId == null ? UUID.randomUUID().toString() : existingId;
        long version = input.expectedVersion() + 1;
        String key = input.apiKey();
        if (existingId == null) {
            if (input.expectedVersion() != 0 || key == null || key.isBlank()) throw new IllegalArgumentException("New connections require a credential and version zero");
            jdbc.update("INSERT INTO oa_model_connection(tenant_id,environment,connection_id,display_name,current_version,created_by) VALUES(?,?,?,?,?,?)",
                    tenant, env, id, input.displayName(), version, RequestContextUtil.getUID());
        } else {
            Map<String,Object> previous = current(tenant, env, id);
            if (((Number) previous.get("version")).longValue() != input.expectedVersion()) throw new OptimisticLockingFailureException("Model configuration changed");
            if (key == null || key.isBlank()) key = secret(tenant, env, id, previous);
            int changed = jdbc.update("UPDATE oa_model_connection SET display_name=?,current_version=? WHERE tenant_id=? AND environment=? AND connection_id=? AND current_version=?",
                    input.displayName(), version, tenant, env, id, input.expectedVersion());
            if (changed != 1) throw new OptimisticLockingFailureException("Model configuration changed");
        }
        var encrypted = cipher.encrypt(scope(tenant, env, id, version), key);
        String credentialRef = UUID.randomUUID().toString();
        jdbc.update("""
                INSERT INTO oa_model_connection_version(tenant_id,environment,connection_id,version,endpoint,model_name,
                supports_stream,supports_tools,supports_usage,connect_timeout_seconds,read_timeout_seconds,total_timeout_seconds,
                ca_ref,credential_ref,credential_key_id,credential_nonce,credential_cipher) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, tenant, env, id, version, input.endpoint(), input.modelName(), input.supportsStream(), input.supportsTools(), input.supportsUsage(),
                input.connectTimeoutSeconds(), input.readTimeoutSeconds(), input.totalTimeoutSeconds(), input.caRef(), credentialRef,
                encrypted.keyId(), encrypted.nonce(), encrypted.ciphertext());
        if (input.revokePrevious()) jdbc.update("UPDATE oa_model_connection_version SET revoked=true WHERE tenant_id=? AND environment=? AND connection_id=? AND version<?", tenant, env, id, version);
        jdbc.update("""
                INSERT INTO oa_tenant_audit(tenant_id,environment,actor_uid,action,subject_id,after_value,retain_until)
                SELECT tenant_id,environment,?,'model.version.created',?,JSON_OBJECT('version',?,'credentialRef',?,'revokedPrevious',?),
                TIMESTAMPADD(DAY,audit_retention_days,CURRENT_TIMESTAMP(3)) FROM oa_tenant_environment WHERE tenant_id=? AND environment=?
                """, RequestContextUtil.getUID(), id, version, credentialRef, input.revokePrevious(), tenant, env);
        return Map.of("connectionId", id, "version", version, "credentialRef", credentialRef);
    }

    private Map<String,Object> current(String tenant, String env, String id) {
        var rows = jdbc.queryForList("SELECT v.* FROM oa_model_connection c JOIN oa_model_connection_version v "
                + "ON v.tenant_id=c.tenant_id AND v.environment=c.environment AND v.connection_id=c.connection_id AND v.version=c.current_version "
                + "WHERE c.tenant_id=? AND c.environment=? AND c.connection_id=? AND c.enabled=true AND v.revoked=false", tenant, env, id);
        if (rows.isEmpty()) throw new TenantAccessDeniedException();
        return rows.getFirst();
    }

    private String secret(String tenant, String env, String id, Map<String,Object> row) {
        return cipher.decrypt(scope(tenant, env, id, ((Number)row.get("version")).longValue()),
                new ModelCredentialCipher.Encrypted((String)row.get("credential_key_id"), (byte[])row.get("credential_nonce"), (byte[])row.get("credential_cipher")));
    }

    static String scope(String tenant, String env, String id, long version) { return tenant + "\0" + env + "\0" + id + "\0" + version; }

    private String caFile(String ref) {
        if (ref == null || ref.isBlank()) return null;
        try {
            Map<String,String> files = json.readValue(environment.getProperty("MODEL_CA_FILES", "{}"), new TypeReference<>() {});
            String file = files.get(ref);
            if (file == null) throw new IllegalArgumentException();
            return file;
        } catch (Exception exception) { throw new IllegalArgumentException("Model CA reference is not approved"); }
    }

    public record BoundModel(OpenAiChatModel model, long version, int totalTimeoutSeconds, boolean tools, boolean stream) {}

    /** Transient response for the authenticated run worker broker, never user APIs or Temporal payloads. */
    public record WorkerModel(String endpoint, String modelName, String credential, long version,
            boolean supportsTools, boolean supportsUsage, int connectTimeoutSeconds, int readTimeoutSeconds,
            int totalTimeoutSeconds, String caRef) {
        @Override public String toString() { return "WorkerModel[credential=REDACTED]"; }
    }

    /** Caller must hold RunLedger.withPermit for the exact deployment's immutable version. */
    public WorkerModel runtimeVersionForAuthorizedLease(String tenant, String env, String id, long version) {
        var rows = jdbc.queryForList("SELECT v.* FROM oa_model_connection_version v JOIN oa_model_connection c "
                + "ON c.tenant_id=v.tenant_id AND c.environment=v.environment AND c.connection_id=v.connection_id "
                + "WHERE v.tenant_id=? AND v.environment=? AND v.connection_id=? AND v.version=? AND v.revoked=false AND c.enabled=true",
                tenant, env, id, version);
        if (rows.isEmpty()) throw new TenantAccessDeniedException();
        var row = rows.getFirst();
        return new WorkerModel((String)row.get("endpoint"), (String)row.get("model_name"), secret(tenant,env,id,row), version,
                Boolean.TRUE.equals(row.get("supports_tools")), Boolean.TRUE.equals(row.get("supports_usage")),
                ((Number)row.get("connect_timeout_seconds")).intValue(), ((Number)row.get("read_timeout_seconds")).intValue(),
                ((Number)row.get("total_timeout_seconds")).intValue(), (String)row.get("ca_ref"));
    }

    public BoundModel forRun(String tenant, String env, String id, boolean needTools, boolean needStream) {
        access.require(tenant, env, TenantPermission.RUN_CREATE);
        Map<String,Object> row = current(tenant, env, id);
        boolean tools = Boolean.TRUE.equals(row.get("supports_tools"));
        boolean stream = Boolean.TRUE.equals(row.get("supports_stream"));
        if ((needTools && !tools) || (needStream && !stream)) throw new IllegalArgumentException("Model capabilities do not meet Agent requirements");
        String endpoint = (String) row.get("endpoint");
        var approved = policy.resolve(endpoint);
        var transport = ModelHttpClients.create(approved, ((Number)row.get("connect_timeout_seconds")).intValue(),
                ((Number)row.get("read_timeout_seconds")).intValue(), caFile((String)row.get("ca_ref")));
        var uri = approved.uri();
        String path = uri.getRawPath();
        if (path == null || path.isBlank() || path.equals("/")) path = "/v1/chat/completions";
        else if (!path.endsWith("/chat/completions")) path = path.replaceAll("/+$", "") + "/chat/completions";
        var api = OpenAiApi.builder().baseUrl(uri.getScheme() + "://" + uri.getRawAuthority()).completionsPath(path)
                .apiKey(secret(tenant, env, id, row)).restClientBuilder(transport.rest()).webClientBuilder(transport.web()).build();
        var options = OpenAiChatOptions.builder().model((String)row.get("model_name")).streamUsage(Boolean.TRUE.equals(row.get("supports_usage"))).build();
        var model = OpenAiChatModel.builder().openAiApi(api).defaultOptions(options)
                .retryTemplate(RetryTemplate.builder().maxAttempts(1).build()).build();
        return new BoundModel(model, ((Number)row.get("version")).longValue(), ((Number)row.get("total_timeout_seconds")).intValue(), tools, stream);
    }

    public Map<String,Object> probe(String tenant, String env, String id) {
        BoundModel bound = forRun(tenant, env, id, false, true);
        long started = System.nanoTime();
        try {
            var responses = bound.model().stream(new Prompt("Reply with OK. This is a platform connection test; do not call tools."))
                    .timeout(Duration.ofSeconds(bound.totalTimeoutSeconds())).collectList().block(Duration.ofSeconds(bound.totalTimeoutSeconds() + 1));
            if (responses == null || responses.isEmpty()) throw new IllegalStateException("Empty model response");
            boolean usage = responses.stream().anyMatch(response -> response.getMetadata().getUsage() != null && response.getMetadata().getUsage().getTotalTokens() > 0);
            return Map.of("ok", true, "version", bound.version(), "stream", true, "usage", usage ? "reported" : "unknown", "durationMs", (System.nanoTime()-started)/1000000,
                    "toolsVerified", false);
        } catch (Exception exception) {
            return Map.of("ok", false, "version", bound.version(), "error", ModelFailure.from(exception).code(), "durationMs", (System.nanoTime()-started)/1000000);
        }
    }
}
