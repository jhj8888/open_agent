package com.iflytek.astron.console.hub.platform.model;

import static org.junit.jupiter.api.Assertions.*;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.time.Instant;
import java.util.List;
import org.junit.jupiter.api.Test;
import org.springframework.mock.env.MockEnvironment;

class ModelTokenBoundPolicyTest {
    private static final Instant NOW=Instant.parse("2026-11-06T00:00:00Z");
    private static final ModelTokenBoundPolicy.Identity ID=new ModelTokenBoundPolicy.Identity("https://model.example/v1","model","deepseek",true);
    private static final String ENTRY="""
            {"endpoint":"https://model.example/v1","modelName":"model","adapter":"deepseek","thinkingEnabled":true,
             "maxTotalTokens":1000,"sourceRef":"model-team/contract-01","evidenceSha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
             "verifiedAt":"2026-11-05T00:00:00Z","validUntil":"2026-11-07T00:00:00Z"}
            """;
    private ModelTokenBoundPolicy policy(String raw) {return new ModelTokenBoundPolicy(new MockEnvironment().withProperty("MODEL_TOKEN_BOUND_CONTRACTS",raw),new ObjectMapper());}

    @Test void snapshotRequiresAnExactCurrentContract() {
        var original=policy("["+ENTRY+"]");var bound=original.capture(ID,NOW,180);
        assertEquals(1000,bound.maxTotalTokens());assertEquals(bound,original.require(ID,bound,NOW,180));
        assertTrue(bound.contractSha256().matches("[a-f0-9]{64}"));
        for(String field:List.of("1000","model-team/contract-01","2026-11-07T00:00:00Z","a".repeat(64))) {
            String replacement=switch(field){case "1000"->"1001";case "model-team/contract-01"->"model-team/contract-02";
                case "2026-11-07T00:00:00Z"->"2026-11-08T00:00:00Z";default->"b".repeat(64);};
            var changed=policy("["+ENTRY.replace(field,replacement)+"]");
            assertThrows(IllegalStateException.class,()->changed.require(ID,bound,NOW,180));
        }
        var removed=policy("[]");assertNull(removed.capture(ID,NOW,180));
        assertThrows(IllegalStateException.class,()->removed.require(ID,bound,NOW,180));
        assertThrows(IllegalStateException.class,()->original.require(ID,null,NOW,180));
    }
    @Test void identityIncludesEndpointModelAdapterAndThinkingMode() {
        var policy=policy("["+ENTRY+"]");
        for(var other:List.of(new ModelTokenBoundPolicy.Identity("https://model.example/v1/","model","deepseek",true),
                new ModelTokenBoundPolicy.Identity(ID.endpoint(),"other","deepseek",true),
                new ModelTokenBoundPolicy.Identity(ID.endpoint(),"model","openai",false),
                new ModelTokenBoundPolicy.Identity(ID.endpoint(),"model","deepseek",false)))assertNull(policy.capture(other,NOW,180));
    }
    @Test void validityCoversTheWholeRequestNotJustAdmission() {
        var policy=policy("["+ENTRY+"]");var until=Instant.parse("2026-11-07T00:00:00Z");
        assertNotNull(policy.capture(ID,until.minusSeconds(181),180));
        assertThrows(IllegalStateException.class,()->policy.capture(ID,until.minusSeconds(180),180));
        assertThrows(IllegalStateException.class,()->policy.capture(ID,Instant.parse("2026-11-04T23:59:59Z"),180));
        assertThrows(IllegalStateException.class,()->policy.capture(ID,until.plusSeconds(1),180));
        assertThrows(IllegalStateException.class,()->policy.capture(ID,NOW,0));
        assertThrows(IllegalStateException.class,()->policy.capture(ID,NOW,3601));
    }
    @Test void rejectsCoercionDuplicateIdentityAndMalformedOrSecretBearingContracts() {
        for(String raw:List.of("null","{}","[] {}","["+ENTRY+","+ENTRY+"]",
                "["+ENTRY.replace("1000","1000.0")+"]","["+ENTRY.replace("1000","\"1000\"")+"]",
                "["+ENTRY.replace("1000","true")+"]","["+ENTRY.replace("1000","0")+"]",
                "["+ENTRY.replace("1000","1000000001")+"]","["+ENTRY.replace("1000","1000,\"maxTotalTokens\":1001")+"]",
                "["+ENTRY.replace("model-team/contract-01","contains secret")+"]",
                "["+ENTRY.replace("https://model.example/v1","https://user:secret@model.example/v1")+"]",
                "["+ENTRY.replace("deepseek","openai")+"]","["+ENTRY.replace("2026-11-07T00:00:00Z","2026-11-05T00:00:00Z")+"]",
                "["+ENTRY.replace("2026-11-07T00:00:00Z","2026-11-07T00:00:00.000001Z")+"]",
                "["+ENTRY.replace("\"maxTotalTokens\":1000","\"maxOutputTokens\":1000")+"]")) {
            var error=assertThrows(IllegalArgumentException.class,()->policy(raw));
            assertEquals("Invalid model token bound contracts",error.getMessage());assertNull(error.getCause());
        }
    }
    @Test void orderingAndEquivalentTimeFormatsDoNotChangeContractIdentity() throws Exception {
        var json=new ObjectMapper();var node=json.readTree(ENTRY);var reordered=json.createObjectNode();
        var fields=new java.util.ArrayList<String>();node.fieldNames().forEachRemaining(fields::add);java.util.Collections.reverse(fields);
        for(String field:fields)reordered.set(field,node.get(field));
        reordered.put("validUntil","2026-11-07T08:00:00+08:00");
        assertEquals(policy("["+ENTRY+"]").capture(ID,NOW,1),policy("["+reordered+"]").capture(ID,NOW,1));
    }
}
