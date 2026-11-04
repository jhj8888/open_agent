package com.iflytek.astron.console.hub.platform.run;

import static org.junit.jupiter.api.Assertions.*;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;

class RunContractTest {
    private final ObjectMapper json = new ObjectMapper();
    @Test void terminalStatesAreAbsorbingAndCancellationHasOnlyOneExit() {
        for (RunState before : RunState.values()) for (RunState next : RunState.values()) {
            if (before.terminal()) assertEquals(before == next, before.canTransitionTo(next));
        }
        assertFalse(RunState.QUEUED.canTransitionTo(RunState.SUCCEEDED));
        assertFalse(RunState.CANCEL_REQUESTED.canTransitionTo(RunState.SUCCEEDED));
        assertTrue(RunState.CANCEL_REQUESTED.canTransitionTo(RunState.CANCELLED));
    }
    @Test void nestedObjectOrderIsCanonicalButArrayOrderIsSignificant() throws Exception {
        assertEquals(RunJson.encode(json, json.readTree("{\"a\":{\"b\":1,\"a\":2},\"b\":[1,2]}"), 1000),
                RunJson.encode(json, json.readTree("{\"b\":[1,2],\"a\":{\"a\":2,\"b\":1}}"), 1000));
        assertNotEquals(RunJson.encode(json, json.readTree("{\"a\":[1,2]}"), 1000),
                RunJson.encode(json, json.readTree("{\"a\":[2,1]}"), 1000));
    }
    @Test void malformedAndOversizedPayloadsAndScopeAreRejected() {
        assertThrows(RunFailure.class, () -> RunJson.encode(json, json.createArrayNode(), 100));
        assertThrows(RunFailure.class, () -> RunJson.encode(json, json.createObjectNode().put("text", "汉".repeat(100)), 200));
        var root = json.createObjectNode(); var nested = root;
        for (int i = 0; i < 34; i++) nested = nested.putObject("next");
        assertEquals("input_too_deep", assertThrows(RunFailure.class, () -> RunJson.encode(json, root, 65536)).code());
        assertThrows(RunFailure.class, () -> new RunLedger.Scope("../tenant", "test"));
        assertThrows(RunFailure.class, () -> new RunLedger.Scope("tenant", "staging"));
    }
}
