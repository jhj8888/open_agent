package com.iflytek.astron.console.hub.platform.model;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.time.Instant;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.HexFormat;
import java.util.List;
import java.util.Set;
import org.springframework.core.env.Environment;

/** Operator-attested provider contracts. Never accepts a bound from a tenant request. */
public final class ModelTokenBoundPolicy {
    private static final Set<String> FIELDS=Set.of("endpoint","modelName","adapter","thinkingEnabled",
            "maxTotalTokens","sourceRef","evidenceSha256","verifiedAt","validUntil");
    public record Identity(String endpoint,String modelName,String adapter,boolean thinkingEnabled) {}
    public record Bound(long maxTotalTokens,String sourceRef,String evidenceSha256,
                        Instant verifiedAt,Instant validUntil,String contractSha256) {}
    private record Contract(Identity identity,Bound bound) {}
    private final List<Contract> contracts;

    public ModelTokenBoundPolicy(Environment environment,ObjectMapper json) {
        try {
            String raw=environment.getProperty("MODEL_TOKEN_BOUND_CONTRACTS","[]");
            if(raw.length()>524288)throw invalid();
            // Reject duplicate JSON properties rather than silently accepting the last bound.
            var reader=json.reader().with(com.fasterxml.jackson.core.JsonParser.Feature.STRICT_DUPLICATE_DETECTION)
                    .with(com.fasterxml.jackson.databind.DeserializationFeature.FAIL_ON_TRAILING_TOKENS);
            JsonNode array=reader.readTree(raw);
            if(array==null || !array.isArray() || array.size()>1000)throw invalid();
            var values=new ArrayList<Contract>();var identities=new HashSet<Identity>();
            for(JsonNode node:array) {
                var names=new HashSet<String>();node.fieldNames().forEachRemaining(names::add);
                if(!node.isObject() || !names.equals(FIELDS))throw invalid();
                String endpoint=text(node,"endpoint",2048),model=text(node,"modelName",255),adapter=text(node,"adapter",16);
                ModelEndpointPolicy.parse(endpoint);
                if(!Set.of("openai","deepseek").contains(adapter) || !node.get("thinkingEnabled").isBoolean())throw invalid();
                boolean thinking=node.get("thinkingEnabled").booleanValue();
                if(thinking&&!adapter.equals("deepseek"))throw invalid();
                var identity=new Identity(endpoint,model,adapter,thinking);
                if(!identities.add(identity))throw invalid();
                var amount=node.get("maxTotalTokens");
                if(!amount.isIntegralNumber() || !amount.canConvertToLong())throw invalid();
                long tokens=amount.longValue();if(tokens<1 || tokens>1_000_000_000L)throw invalid();
                String source=text(node,"sourceRef",128),evidence=text(node,"evidenceSha256",64);
                if(!source.matches("[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}") || !evidence.matches("[a-f0-9]{64}"))throw invalid();
                Instant verified=instant(node,"verifiedAt"),until=instant(node,"validUntil");
                if(!verified.isBefore(until))throw invalid();
                String canonical=json.writeValueAsString(List.of("model-token-bound-v1",endpoint,model,adapter,thinking,
                        tokens,source,evidence,verified.toString(),until.toString()));
                String hash=HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(canonical.getBytes(StandardCharsets.UTF_8)));
                values.add(new Contract(identity,new Bound(tokens,source,evidence,verified,until,hash)));
            }
            contracts=List.copyOf(values);
        } catch(Exception error) {throw invalid();} // Never expose deployment configuration in errors.
    }

    public boolean isEmpty() {return contracts.isEmpty();}

    /** Null means no approved contract, never permission to run in strict mode. */
    public Bound capture(Identity identity,Instant databaseNow,int totalTimeoutSeconds) {
        for(var contract:contracts)if(contract.identity().equals(identity)) {
            validAt(contract.bound(),databaseNow,totalTimeoutSeconds);return contract.bound();
        }
        return null;
    }

    /** Exact snapshot match prevents changing an existing version's meaning through ops config. */
    public Bound require(Identity identity,Bound snapshot,Instant databaseNow,int totalTimeoutSeconds) {
        if(snapshot==null)throw unavailable();
        var current=capture(identity,databaseNow,totalTimeoutSeconds);
        if(!snapshot.equals(current))throw unavailable();
        return snapshot;
    }

    private static void validAt(Bound bound,Instant now,int timeout) {
        if(now==null || timeout<1 || timeout>3600 || now.isBefore(bound.verifiedAt())
                || !now.plusSeconds(timeout).isBefore(bound.validUntil()))throw unavailable();
    }
    private static String text(JsonNode node,String field,int length) {
        var value=node.get(field);
        if(value==null || !value.isTextual())throw invalid();
        String result=value.textValue();
        if(result.isBlank() || result.length()>length || result.chars().anyMatch(c->c<32 || c==127))throw invalid();
        return result;
    }
    private static Instant instant(JsonNode node,String field) {
        Instant value=Instant.parse(text(node,field,40));
        if(value.getNano()%1_000_000!=0 || value.isBefore(Instant.parse("2000-01-01T00:00:00Z"))
                || !value.isBefore(Instant.parse("2100-01-01T00:00:00Z")))throw invalid();
        return value;
    }
    private static IllegalArgumentException invalid() {return new IllegalArgumentException("Invalid model token bound contracts");}
    private static IllegalStateException unavailable() {return new IllegalStateException("Trusted model token bound unavailable");}
}
