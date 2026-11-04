package com.iflytek.astron.console.hub.platform.run;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.*;
import java.util.*;
import org.junit.jupiter.api.*;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.security.oauth2.server.resource.authentication.JwtAuthenticationToken;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

class RunEventStreamsTest {
    private static final String RUN="fc42b1dd-3f87-400c-a38b-2161cfdd988a";
    private final AgentRunService runs=mock(AgentRunService.class);
    private final RunEventStreams streams=new RunEventStreams(runs);
    @BeforeEach void identity() {
        SecurityContextHolder.getContext().setAuthentication(new JwtAuthenticationToken(
                Jwt.withTokenValue("test").header("alg","RS256").subject("alice").build(),List.of()));
    }
    @AfterEach void close() { streams.stop();SecurityContextHolder.clearContext(); }
    @Test void failedRunSendsCorrelatedErrorAfterPersistedEventsAndBeforeStreamEnd() throws Exception {
        var event=Map.<String,Object>of("sequence_no",3L,"event_type","run.state","run_id",RUN,"payload",Map.of("state","failed"));
        when(runs.events(any(),eq(RUN),eq(2L))).thenReturn(new AgentRunService.Events(List.of(event),3,true));
        when(runs.status(any(),eq(RUN))).thenReturn(Map.of("state","failed","error_code","model_failed"));
        var mvc=MockMvcBuilders.standaloneSetup(new AgentRunController(runs,streams)).build();
        var result=mvc.perform(get("/api/v1/runs/"+RUN+"/events?after=2").accept("text/event-stream")).andReturn();
        result.getAsyncResult(10000);
        String body=mvc.perform(asyncDispatch(result)).andReturn().getResponse().getContentAsString();
        assertTrue(body.contains("id:3"));assertTrue(body.contains("model_failed"));assertTrue(body.contains(RUN));
        assertTrue(body.indexOf("event:run.state")<body.indexOf("event:error"));
        assertTrue(body.indexOf("event:error")<body.indexOf("event:stream.end"));
    }
    @Test void revocationAfterPreflightStopsSubscriptionWithoutLeakingFurtherEvents() throws Exception {
        when(runs.events(any(),eq(RUN),eq(0L))).thenReturn(new AgentRunService.Events(List.of(),0,false))
                .thenThrow(new RunFailure("resource_unavailable"));
        var mvc=MockMvcBuilders.standaloneSetup(new AgentRunController(runs,streams)).build();
        var result=mvc.perform(get("/api/v1/runs/"+RUN+"/events").accept("text/event-stream")).andReturn();
        result.getAsyncResult(10000);
        String body=mvc.perform(asyncDispatch(result)).andReturn().getResponse().getContentAsString();
        assertTrue(body.contains("event:error"));assertTrue(body.contains("resource_unavailable"));
        assertFalse(body.contains("event:stream.end"));verify(runs,never()).status(any(),any());
    }
}
