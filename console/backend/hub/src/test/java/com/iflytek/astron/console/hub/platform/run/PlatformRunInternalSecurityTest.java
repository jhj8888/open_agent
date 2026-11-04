package com.iflytek.astron.console.hub.platform.run;

import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;

import com.iflytek.astron.console.hub.config.PlatformInternalSecurityConfig;
import com.iflytek.astron.console.hub.platform.PlatformExceptionHandler;
import java.util.Optional;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.boot.test.mock.mockito.MockBean;
import org.springframework.context.annotation.Import;
import org.springframework.test.web.servlet.MockMvc;

@WebMvcTest(controllers={PlatformRunInternalController.class,PlatformRunRuntimeController.class}, properties={
        "PLATFORM_SERVICE_KEYS={\"worker-one\":{\"role\":\"worker\",\"secret\":\"wwwwwwwwwwwwwwwwwwwwwwwwwwwwwwww\"},\"dispatcher-one\":{\"role\":\"dispatcher\",\"secret\":\"dddddddddddddddddddddddddddddddd\"}}"
})
@Import({PlatformInternalSecurityConfig.class, PlatformExceptionHandler.class})
class PlatformRunInternalSecurityTest {
    @Autowired MockMvc mvc;
    @MockBean RunLedger ledger;
    @MockBean RunOutbox outbox;
    @MockBean RunRuntimeService runtime;
    @MockBean com.iflytek.astron.console.commons.data.UserInfoDataService users;
    private static final String RUN="34fd353e-6cb4-43fd-86a6-6e94c220421a";
    private static final String CLAIM="/internal/platform/worker/runs/"+RUN+"/claim";
    private static final String SCOPE="{\"tenant\":\"space-10\",\"environment\":\"test\"}";

    @Test void missingWrongAndUserBearerCredentialsCannotEnterWorkerApi() throws Exception {
        mvc.perform(post(CLAIM).contentType("application/json").content(SCOPE)).andExpect(status().isUnauthorized());
        mvc.perform(post(CLAIM).header("Authorization","Bearer any-user-jwt").contentType("application/json").content(SCOPE)).andExpect(status().isUnauthorized());
        mvc.perform(post(CLAIM).header("X-Platform-Key-Id","worker-one").header("X-Platform-Service-Key","invalid")
                .contentType("application/json").content(SCOPE)).andExpect(status().isUnauthorized());
        verifyNoInteractions(ledger, outbox);
    }
    @Test void dispatcherAndWorkerRolesAreNotInterchangeable() throws Exception {
        mvc.perform(post(CLAIM).header("X-Platform-Key-Id","dispatcher-one").header("X-Platform-Service-Key","d".repeat(32))
                .contentType("application/json").content(SCOPE)).andExpect(status().isForbidden());
        mvc.perform(post("/internal/platform/dispatch/claim").header("X-Platform-Key-Id","worker-one").header("X-Platform-Service-Key","w".repeat(32))
                .contentType("application/json").content("{\"limit\":1,\"leaseSeconds\":30}")).andExpect(status().isForbidden());
        verifyNoInteractions(ledger, outbox);
    }
    @Test void authenticatedIdentityBindsClaimAndResponseIsNotCached() throws Exception {
        when(ledger.claim(any(),eq(RUN),eq("worker-one"))).thenReturn(Optional.empty());
        mvc.perform(post(CLAIM).header("X-Platform-Key-Id","worker-one").header("X-Platform-Service-Key","w".repeat(32))
                .contentType("application/json").content(SCOPE)).andExpect(status().isOk())
                .andExpect(header().string("Cache-Control","no-store")).andExpect(jsonPath("$.lease").doesNotExist());
        verify(ledger).claim(new RunLedger.Scope("space-10","test"),RUN,"worker-one");
    }
    @Test void malformedScopeFailsBeforeStorageAndTrailingSlashCannotBypassServiceAuth() throws Exception {
        mvc.perform(post(CLAIM).header("X-Platform-Key-Id","worker-one").header("X-Platform-Service-Key","w".repeat(32))
                .contentType("application/json").content("{\"tenant\":\"../escape\",\"environment\":\"test\"}")).andExpect(status().isBadRequest());
        mvc.perform(post(CLAIM+"/").header("Authorization","Bearer user").contentType("application/json").content(SCOPE))
                .andExpect(status().isUnauthorized());
        verifyNoInteractions(ledger, outbox);
    }

    @Test void runtimeCredentialAccessRequiresWorkerRoleAndAuthenticatedLeaseOwner() throws Exception {
        String body="{\"tenant\":\"space-10\",\"environment\":\"test\",\"attempt\":1,\"leaseId\":\"fc42b1dd-3f87-400c-a38b-2161cfdd988a\",\"fencingToken\":1}";
        String path="/internal/platform/worker/runs/"+RUN+"/runtime";
        mvc.perform(post(path).contentType("application/json").content(body)).andExpect(status().isUnauthorized());
        mvc.perform(post(path).header("X-Platform-Key-Id","dispatcher-one").header("X-Platform-Service-Key","d".repeat(32))
                .contentType("application/json").content(body)).andExpect(status().isForbidden());
        verifyNoInteractions(runtime);
        when(runtime.bundle(any())).thenThrow(new RunFailure("lease_fenced"));
        mvc.perform(post(path).header("X-Platform-Key-Id","worker-one").header("X-Platform-Service-Key","w".repeat(32))
                .contentType("application/json").content(body)).andExpect(status().isConflict());
        verify(runtime).bundle(new RunLedger.Lease(new RunLedger.Scope("space-10","test"),RUN,1,"fc42b1dd-3f87-400c-a38b-2161cfdd988a",1,"worker-one"));
    }

    @Test void perOperationPermitUsesAuthenticatedOwnerAndNeverFallsBackToUserJwt() throws Exception {
        String body="{\"tenant\":\"space-10\",\"environment\":\"test\",\"attempt\":1,\"leaseId\":\"fc42b1dd-3f87-400c-a38b-2161cfdd988a\",\"fencingToken\":1}";
        String path="/internal/platform/worker/runs/"+RUN+"/permit";
        mvc.perform(post(path).header("Authorization","Bearer user-jwt").contentType("application/json").content(body))
                .andExpect(status().isUnauthorized());
        when(ledger.withPermit(any(),any())).thenReturn(java.util.Map.of("permitted",true));
        mvc.perform(post(path).header("X-Platform-Key-Id","worker-one").header("X-Platform-Service-Key","w".repeat(32))
                .contentType("application/json").content(body)).andExpect(status().isOk()).andExpect(jsonPath("$.permitted").value(true));
        verify(ledger).withPermit(eq(new RunLedger.Lease(new RunLedger.Scope("space-10","test"),RUN,1,
                "fc42b1dd-3f87-400c-a38b-2161cfdd988a",1,"worker-one")),any());
        when(ledger.withPermit(any(),any())).thenThrow(new RunFailure("lease_fenced"));
        mvc.perform(post(path).header("X-Platform-Key-Id","worker-one").header("X-Platform-Service-Key","w".repeat(32))
                .contentType("application/json").content(body)).andExpect(status().isConflict());
    }
}
