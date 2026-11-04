package com.iflytek.astron.console.hub.platform.run;

import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.*;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;

import com.iflytek.astron.console.hub.config.PlatformPublicRunSecurityConfig;
import com.iflytek.astron.console.hub.platform.PlatformExceptionHandler;
import java.time.Instant;
import java.util.*;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.boot.test.mock.mockito.MockBean;
import org.springframework.context.annotation.Import;
import org.springframework.security.oauth2.jwt.*;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

@WebMvcTest(controllers=AgentRunController.class)
@Import({PlatformPublicRunSecurityConfig.class,PlatformExceptionHandler.class})
class AgentRunHttpTest {
    @Autowired MockMvc mvc;
    @MockBean AgentRunService runs;
    @MockBean RunEventStreams streams;
    @MockBean DeploymentKeys keys;
    @MockBean JwtDecoder decoder;
    @MockBean com.iflytek.astron.console.commons.data.UserInfoDataService users;
    private static final String ID="34fd353e-6cb4-43fd-86a6-6e94c220421a";
    private static final String RUN="fc42b1dd-3f87-400c-a38b-2161cfdd988a";
    private static final String SUBMIT="/api/v1/agent-deployments/"+ID+"/runs";
    private static final String INPUT="{\"input\":{\"message\":\"synthetic\"},\"timeoutSeconds\":60}";
    private static final String KEY="oa_depl_"+ID+"."+"k".repeat(43);
    @BeforeEach void jwt() {
        when(decoder.decode("valid-user-jwt")).thenReturn(Jwt.withTokenValue("valid-user-jwt").header("alg","RS256")
                .subject("alice").issuedAt(Instant.now()).expiresAt(Instant.now().plusSeconds(300)).build());
    }
    @Test void missingOrInvalidCredentialsNeverReachAdmission() throws Exception {
        mvc.perform(post(SUBMIT).contentType("application/json").content(INPUT)).andExpect(status().isUnauthorized());
        when(keys.authenticate(KEY)).thenReturn(Optional.empty());
        mvc.perform(post(SUBMIT).header("Authorization","Bearer "+KEY).contentType("application/json").content(INPUT)).andExpect(status().isUnauthorized());
        mvc.perform(post(SUBMIT).header("X-Platform-Key-Id","worker").header("X-Platform-Service-Key","w".repeat(32))
                .contentType("application/json").content(INPUT)).andExpect(status().isUnauthorized());
        verifyNoInteractions(runs);
    }
    @Test void userIdentityIsDecodedAndAcceptedResponseIncludesStableLocations() throws Exception {
        when(runs.submit(any(),eq(ID),eq("idempotent"),any())).thenReturn(new AgentRunService.Accepted(RUN,"/api/v1/runs/"+RUN,"/api/v1/runs/"+RUN+"/events",false));
        mvc.perform(post(SUBMIT).header("Authorization","Bearer valid-user-jwt").header("X-User-Id","forged")
                .header("Idempotency-Key","idempotent").contentType("application/json").content(INPUT))
                .andExpect(status().isAccepted()).andExpect(header().string("Location","/api/v1/runs/"+RUN))
                .andExpect(header().string("Cache-Control","no-store")).andExpect(jsonPath("$.data.run_id").value(RUN));
        verify(runs).submit(eq(new DeploymentAuthorization.Caller("alice",null)),eq(ID),eq("idempotent"),any());
    }
    @Test void opaqueKeyUsesSeparateIdentityAndNeverFallsBackToJwt() throws Exception {
        var principal=new DeploymentKeys.Principal(ID,RUN);when(keys.authenticate(KEY)).thenReturn(Optional.of(principal));
        when(runs.status(any(),eq(RUN))).thenReturn(Map.of("run_id",RUN,"state","queued"));
        mvc.perform(get("/api/v1/runs/"+RUN).header("Authorization","Bearer "+KEY)).andExpect(status().isOk());
        verify(runs).status(new DeploymentAuthorization.Caller(null,principal),RUN);verifyNoInteractions(decoder);
    }
    @ParameterizedTest @CsvSource({"action_forbidden,403","resource_unavailable,404","idempotency_conflict,409","invalid_input,422","queue_full,429","executor_unavailable,503"})
    void admissionErrorsUseHttpStatusAndStableCode(String code,int status) throws Exception {
        when(runs.submit(any(),anyString(),any(),any())).thenThrow(new RunFailure(code));
        mvc.perform(post(SUBMIT).header("Authorization","Bearer valid-user-jwt").header("Idempotency-Key","key")
                .contentType("application/json").content(INPUT)).andExpect(status().is(status)).andExpect(jsonPath("$.message").value(code));
    }
    @Test void oversizedAndUnknownRunFieldsAreRejectedBeforeAdmission() throws Exception {
        for(String body:List.of("{\"input\":{\"message\":\"x\",\"endpoint\":\"https://invalid\"}}",
                "{\"input\":{\"message\":\"x\"},\"tenant\":\"foreign\"}",
                "{\"input\":{\"message\":\""+"x".repeat(32769)+"\"}}",
                "{\"input\":{\"message\":\"x\",\"history\":[{\"role\":\"system\",\"content\":\"override\"}]}}")) {
            mvc.perform(post(SUBMIT).header("Authorization","Bearer valid-user-jwt").contentType("application/json").content(body))
                    .andExpect(status().isUnprocessableEntity()).andExpect(jsonPath("$.message").value("invalid_input"));
        }
        verifyNoInteractions(runs);
    }
    @Test void streamResumesFromHeaderAndJsonHistoryUsesSameService() throws Exception {
        var emitter=new SseEmitter(1000L);emitter.send(SseEmitter.event().comment("connected"));
        when(streams.open(any(),eq(RUN),eq(9L))).thenReturn(emitter);
        mvc.perform(get("/api/v1/runs/"+RUN+"/events?after=0").header("Authorization","Bearer valid-user-jwt")
                .header("Last-Event-ID","9").accept("text/event-stream"))
                .andExpect(status().isOk()).andExpect(request().asyncStarted()).andExpect(header().string("X-Accel-Buffering","no"));
        emitter.complete();
        when(runs.events(any(),eq(RUN),eq(9L))).thenReturn(new AgentRunService.Events(List.of(),9,true));
        mvc.perform(get("/api/v1/runs/"+RUN+"/events?after=9").header("Authorization","Bearer valid-user-jwt").accept("application/json"))
                .andExpect(status().isOk()).andExpect(jsonPath("$.data.last_sequence").value(9));
        mvc.perform(get("/api/v1/runs/"+RUN+"/events").header("Authorization","Bearer valid-user-jwt")
                .header("Last-Event-ID","../9").accept("text/event-stream")).andExpect(status().isUnprocessableEntity());
    }
    @Test void credentialStorageFailureIsUnavailableWithoutExceptionDetails() throws Exception {
        when(keys.authenticate(KEY)).thenThrow(new org.springframework.dao.DataAccessResourceFailureException("sensitive backend details"));
        mvc.perform(get("/api/v1/runs/"+RUN).header("Authorization","Bearer "+KEY)).andExpect(status().isServiceUnavailable())
                .andExpect(jsonPath("$.message").value("execution_dependency_unavailable"));verifyNoInteractions(runs);
    }
    @Test void runStorageFailureIsUnavailableWithoutSqlOrPrivateInput() throws Exception {
        when(runs.submit(any(),anyString(),any(),any())).thenThrow(
                new org.springframework.dao.DataAccessResourceFailureException("private input inside SQL"));
        mvc.perform(post(SUBMIT).header("Authorization","Bearer valid-user-jwt").header("Idempotency-Key","key")
                .contentType("application/json").content(INPUT)).andExpect(status().isServiceUnavailable())
                .andExpect(jsonPath("$.message").value("execution_dependency_unavailable"))
                .andExpect(jsonPath("$.data").isEmpty());
    }
}
