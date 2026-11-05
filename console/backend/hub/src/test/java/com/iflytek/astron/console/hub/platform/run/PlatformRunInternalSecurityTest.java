package com.iflytek.astron.console.hub.platform.run;

import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
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

@WebMvcTest(controllers={PlatformRunInternalController.class,PlatformRunRuntimeController.class,PlatformRunSandboxController.class,PlatformRunReconcileController.class,PlatformClusterReconcileController.class,com.iflytek.astron.platform.OperationsMetricsController.class}, properties={
        "PLATFORM_SERVICE_KEYS={\"observer-one\":{\"role\":\"observer\",\"secret\":\"oooooooooooooooooooooooooooooooo\"},\"worker-one\":{\"role\":\"worker\",\"secret\":\"wwwwwwwwwwwwwwwwwwwwwwwwwwwwwwww\"},\"dispatcher-one\":{\"role\":\"dispatcher\",\"secret\":\"dddddddddddddddddddddddddddddddd\"},\"supervisor-one\":{\"role\":\"reconciler\",\"secret\":\"ssssssssssssssssssssssssssssssss\"}}"
})
@Import({PlatformInternalSecurityConfig.class, PlatformExceptionHandler.class,com.iflytek.astron.platform.OperationsMetricsController.class})
class PlatformRunInternalSecurityTest {
    @Autowired MockMvc mvc;
    @MockBean RunLedger ledger;
    @MockBean RunOutbox outbox;
    @MockBean RunRuntimeService runtime;
    @MockBean RunSandboxJournal journal;
    @MockBean RunSupervision supervision;
    @MockBean RunClusterSupervision clusters;
    @MockBean com.iflytek.astron.platform.OperationsMetrics metrics;

    @Test void metricsObserverIsReadOnlyAndCannotEnterExecutionRoutes() throws Exception {
        String path="/internal/platform/metrics";
        mvc.perform(get(path).header("Authorization","Bearer user-jwt")).andExpect(status().isUnauthorized());
        mvc.perform(get(path).header("X-Platform-Key-Id","worker-one").header("X-Platform-Service-Key","w".repeat(32)))
                .andExpect(status().isForbidden());
        mvc.perform(post(CLAIM).header("X-Platform-Key-Id","observer-one").header("X-Platform-Service-Key","o".repeat(32))
                .contentType("application/json").content(SCOPE)).andExpect(status().isForbidden());
        mvc.perform(post(path).header("X-Platform-Key-Id","observer-one").header("X-Platform-Service-Key","o".repeat(32)))
                .andExpect(status().isForbidden());
        verifyNoInteractions(metrics,ledger);
        when(metrics.scrape()).thenReturn("open_agent_metrics_collection_success 1.0\n");
        mvc.perform(get(path).header("X-Platform-Key-Id","observer-one").header("X-Platform-Service-Key","o".repeat(32)))
                .andExpect(status().isOk()).andExpect(header().string("Cache-Control","no-store"))
                .andExpect(content().string("open_agent_metrics_collection_success 1.0\n"));
    }
    @MockBean com.iflytek.astron.console.commons.data.UserInfoDataService users;
    private static final String RUN="34fd353e-6cb4-43fd-86a6-6e94c220421a";
    private static final String CLAIM="/internal/platform/worker/runs/"+RUN+"/claim";
    private static final String SCOPE="{\"tenant\":\"space-10\",\"environment\":\"test\"}";

    @Test void clusterRoutesRejectUserWorkerDispatcherAndObserverIdentities() throws Exception {
        String id="fc42b1dd-3f87-400c-a38b-2161cfdd988a";
        var paths=java.util.List.of("runs/"+RUN+"/claim","runs/"+RUN+"/lookup","runs/"+RUN+"/abandon","pending",
                "attempts/"+id+"/inspect","attempts/"+id+"/begin","attempts/"+id+"/observed","attempts/"+id+"/terminated",
                "attempts/"+id+"/fence","attempts/"+id+"/not-started","attempts/"+id+"/sandbox-observed","attempts/"+id+"/release");
        for(String path:paths) {
            String url="/internal/platform/reconcile/cluster/"+path;
            mvc.perform(post(url).header("Authorization","Bearer user").contentType("application/json").content("{}"))
                    .andExpect(status().isUnauthorized());
            for(var identity:java.util.Map.of("worker-one","w","dispatcher-one","d","observer-one","o").entrySet())
                mvc.perform(post(url).header("X-Platform-Key-Id",identity.getKey()).header("X-Platform-Service-Key",identity.getValue().repeat(32))
                        .contentType("application/json").content("{}")).andExpect(status().isForbidden());
        }
        verifyNoInteractions(clusters,ledger,journal);
    }

    @Test void clusterControllerUsesVerifiedServicePrincipalAndRejectsInvalidScope() throws Exception {
        String route="/internal/platform/reconcile/cluster/runs/"+RUN+"/claim";
        when(clusters.claim(any(),eq(RUN),eq("supervisor-one"))).thenReturn(new RunClusterSupervision.Claim(null,"queued",null));
        mvc.perform(post(route).header("X-Platform-Key-Id","supervisor-one").header("X-Platform-Service-Key","s".repeat(32))
                .header("X-User-Id","forged").contentType("application/json").content(SCOPE)).andExpect(status().isOk());
        verify(clusters).claim(new RunLedger.Scope("space-10","test"),RUN,"supervisor-one");
        mvc.perform(post(route).header("X-Platform-Key-Id","supervisor-one").header("X-Platform-Service-Key","s".repeat(32))
                .contentType("application/json").content("{\"tenant\":\"space-10\",\"environment\":\"anything\"}"))
                .andExpect(status().isBadRequest());
        verifyNoMoreInteractions(clusters);
    }

    @Test void workerDispatcherAndUserCannotEnterAnyReconcilerOperation() throws Exception {
        String id="fc42b1dd-3f87-400c-a38b-2161cfdd988a";
        var paths=java.util.List.of("runs/"+RUN+"/claim","runs/"+RUN+"/lookup","runs/"+RUN+"/abandon","pending","attempts/"+id+"/inspect","attempts/"+id+"/begin",
                "attempts/"+id+"/observed","attempts/"+id+"/fence","attempts/"+id+"/sandbox-observed","attempts/"+id+"/release");
        for(String path:paths) {
            String url="/internal/platform/reconcile/"+path;
            mvc.perform(post(url).header("Authorization","Bearer user").contentType("application/json").content("{}"))
                    .andExpect(status().isUnauthorized());
            for(var identity:java.util.Map.of("worker-one","w","dispatcher-one","d").entrySet())
                mvc.perform(post(url).header("X-Platform-Key-Id",identity.getKey()).header("X-Platform-Service-Key",identity.getValue().repeat(32))
                        .contentType("application/json").content("{}")).andExpect(status().isForbidden());
        }
        verifyNoInteractions(supervision,ledger,journal);
    }

    @Test void reconcilerIdentityComesFromAuthenticationAndCannotReadWorkerSecrets() throws Exception {
        String id="fc42b1dd-3f87-400c-a38b-2161cfdd988a";
        mvc.perform(post("/internal/platform/reconcile/attempts/"+id+"/fence")
                .header("X-Platform-Key-Id","supervisor-one").header("X-Platform-Service-Key","s".repeat(32)))
                .andExpect(status().isOk()).andExpect(header().string("Cache-Control","no-store"));
        verify(supervision).fence(id,"supervisor-one");
        mvc.perform(post("/internal/platform/worker/runs/"+RUN+"/runtime")
                .header("X-Platform-Key-Id","supervisor-one").header("X-Platform-Service-Key","s".repeat(32))
                .contentType("application/json").content("{}"))
                .andExpect(status().isForbidden());
        verifyNoInteractions(runtime);
        when(supervision.inspect(id,"supervisor-one")).thenThrow(new RunFailure("resource_unavailable"));
        mvc.perform(post("/internal/platform/reconcile/attempts/"+id+"/inspect")
                .header("X-Platform-Key-Id","supervisor-one").header("X-Platform-Service-Key","s".repeat(32)))
                .andExpect(status().isNotFound());
    }

    @Test void sandboxJournalRequiresServiceIdentityAndNeverAcceptsQuotaReleaseFromWorker() throws Exception {
        String lease="{\"tenant\":\"space-10\",\"environment\":\"test\",\"attempt\":1,\"leaseId\":\"fc42b1dd-3f87-400c-a38b-2161cfdd988a\",\"fencingToken\":1}";
        String image="registry.internal/skill@sha256:"+"a".repeat(64);
        String body="{\"lease\":"+lease+",\"profile\":\"gvisor-demo\",\"image\":\""+image+"\"}";
        String path="/internal/platform/worker/runs/"+RUN+"/sandbox/";
        mvc.perform(post(path+"reserve").header("Authorization","Bearer user").contentType("application/json").content(body))
                .andExpect(status().isUnauthorized());
        mvc.perform(post(path+"reserve").header("X-Platform-Key-Id","dispatcher-one").header("X-Platform-Service-Key","d".repeat(32))
                .contentType("application/json").content(body)).andExpect(status().isForbidden());
        verifyNoInteractions(journal);
        mvc.perform(post(path+"reserve").header("X-Platform-Key-Id","worker-one").header("X-Platform-Service-Key","w".repeat(32))
                .contentType("application/json").content(body)).andExpect(status().isOk()).andExpect(jsonPath("$.accepted").value(true));
        var expected=new RunLedger.Lease(new RunLedger.Scope("space-10","test"),RUN,1,"fc42b1dd-3f87-400c-a38b-2161cfdd988a",1,"worker-one");
        verify(journal).reserve(expected,"gvisor-demo",image);
        String id="8fd253b5-d238-4cd6-88c6-d50e2df70ed5";
        String observation="{\"lease\":"+lease+",\"sandboxId\":\""+id+"\"}";
        mvc.perform(post(path+"destruction-reported").header("X-Platform-Key-Id","worker-one").header("X-Platform-Service-Key","w".repeat(32))
                .contentType("application/json").content(observation)).andExpect(status().isOk());
        verify(journal).destructionReported(expected,id);
        mvc.perform(post(path+"release-verified").header("X-Platform-Key-Id","worker-one").header("X-Platform-Service-Key","w".repeat(32))
                .contentType("application/json").content(observation)).andExpect(status().isNotFound());
        verify(journal,never()).releaseVerified(any(),anyString(),anyString());
        verify(ledger,never()).resourcesReleased(any());
    }

    @Test void sandboxQuotaAndCreationConflictHaveExplicitHttpStatuses() throws Exception {
        String body="{\"lease\":{\"tenant\":\"space-10\",\"environment\":\"test\",\"attempt\":1,\"leaseId\":\"fc42b1dd-3f87-400c-a38b-2161cfdd988a\",\"fencingToken\":1},\"profile\":\"gvisor-demo\",\"image\":\"registry.internal/x@sha256:"+"a".repeat(64)+"\"}";
        for (var failure : java.util.Map.of("sandbox_quota_full",429,"sandbox_state_conflict",409).entrySet()) {
            doThrow(new RunFailure(failure.getKey())).when(journal).reserve(any(),anyString(),anyString());
            mvc.perform(post("/internal/platform/worker/runs/"+RUN+"/sandbox/reserve").header("X-Platform-Key-Id","worker-one")
                    .header("X-Platform-Service-Key","w".repeat(32)).contentType("application/json").content(body))
                    .andExpect(status().is(failure.getValue()));
        }
    }

    @Test void missingWrongAndUserBearerCredentialsCannotEnterWorkerApi() throws Exception {
        mvc.perform(post(CLAIM).contentType("application/json").content(SCOPE)).andExpect(status().isUnauthorized());
        mvc.perform(post(CLAIM).header("Authorization","Bearer any-user-jwt").contentType("application/json").content(SCOPE)).andExpect(status().isUnauthorized());
        mvc.perform(post(CLAIM).header("X-Platform-Key-Id","worker-one").header("X-Platform-Service-Key","invalid")
                .contentType("application/json").content(SCOPE)).andExpect(status().isUnauthorized());
        verifyNoInteractions(ledger, outbox);
    }
    @Test void dispatcherMustSelectOneEnvironmentAndLegacyRequestsAreTestOnly() throws Exception {
        String path="/internal/platform/dispatch/claim";
        for(String environment:java.util.List.of("test","prod")) {
            mvc.perform(post(path).header("X-Platform-Key-Id","dispatcher-one").header("X-Platform-Service-Key","d".repeat(32))
                    .contentType("application/json").content("{\"limit\":4,\"leaseSeconds\":60,\"environment\":\""+environment+"\"}"))
                    .andExpect(status().isOk());
            verify(outbox).claim(environment,4,60);
        }
        mvc.perform(post(path).header("X-Platform-Key-Id","dispatcher-one").header("X-Platform-Service-Key","d".repeat(32))
                .contentType("application/json").content("{\"limit\":2,\"leaseSeconds\":30}")).andExpect(status().isOk());
        verify(outbox).claim("test",2,30);
    }

    @Test void malformedDispatcherEnvironmentCannotReachStorage() throws Exception {
        for(String environment:java.util.List.of("","all","TEST","../prod")) {
            mvc.perform(post("/internal/platform/dispatch/claim").header("X-Platform-Key-Id","dispatcher-one")
                    .header("X-Platform-Service-Key","d".repeat(32)).contentType("application/json")
                    .content("{\"limit\":4,\"leaseSeconds\":60,\"environment\":\""+environment+"\"}"))
                    .andExpect(status().isBadRequest());
        }
        verifyNoInteractions(outbox);
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
