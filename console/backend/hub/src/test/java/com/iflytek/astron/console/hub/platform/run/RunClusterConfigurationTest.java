package com.iflytek.astron.console.hub.platform.run;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.mock;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.util.LinkedHashMap;
import java.util.Map;
import org.junit.jupiter.api.Test;
import org.springframework.mock.env.MockEnvironment;
import org.springframework.transaction.PlatformTransactionManager;

class RunClusterConfigurationTest {
    private final ObjectMapper json = new ObjectMapper();
    private Map<String,Object> target() {
        return new LinkedHashMap<>(Map.of("workerId","executor","environment","test",
                "clusterId","5d93bc29-5ae4-4fce-8f06-5d2ebd121c09","namespace","executors-test",
                "namespaceUid","a9388d6f-67c0-4d99-ae2c-75b6bb924154","executorImage","registry.invalid/worker@sha256:"+"a".repeat(64)));
    }
    private MockEnvironment environment(Map<String,?> configured) {
        return new MockEnvironment().withProperty("PLATFORM_CLUSTER_RECONCILERS",json.valueToTree(configured).toString())
                .withProperty("PLATFORM_SERVICE_KEYS",json.valueToTree(Map.of(
                        "supervisor",Map.of("role","reconciler","secret","s".repeat(32)),
                        "executor",Map.of("role","worker","secret","w".repeat(32)),
                        "supervisor2",Map.of("role","reconciler","secret","t".repeat(32)),
                        "executor2",Map.of("role","worker","secret","v".repeat(32)))).toString());
    }
    private RunClusterSupervision instance(MockEnvironment config) {
        return new RunClusterSupervision(null,null,null,mock(PlatformTransactionManager.class),json,config);
    }
    private void invalid(MockEnvironment config) {
        assertEquals("Invalid platform cluster supervisor bindings",assertThrows(IllegalStateException.class,()->instance(config)).getMessage());
    }
    @Test void absentConfigurationLeavesClusterAdmissionUnavailable() {
        var service=instance(new MockEnvironment());
        assertEquals("action_forbidden",assertThrows(RunFailure.class,()->service.claim(new RunLedger.Scope("tenant","test"),
                "05d418f2-3690-4861-9ecf-07a42c1079bf","supervisor")).code());
    }
    @Test void configuredFixedIdentityIsAccepted() { assertNotNull(instance(environment(Map.of("supervisor",target())))); }
    @Test void mutableImagesInvalidNamesAndUnknownConfigurationKeysAreRejected() {
        for(var change:Map.of("executorImage","registry.invalid/worker:latest","namespace","with.dot","clusterId","not-a-uuid",
                "environment","staging","namespaceUid","unknown","unexpectedOption","ignored").entrySet()) {
            var target=target();target.put(change.getKey(),change.getValue());invalid(environment(Map.of("supervisor",target)));
        }
    }
    @Test void legacyReconcilerAndWorkerBindingsCannotBeReused() {
        invalid(environment(Map.of("supervisor",target())).withProperty("PLATFORM_RECONCILER_WORKERS","{\"supervisor\":\"executor2\"}"));
        invalid(environment(Map.of("supervisor",target())).withProperty("PLATFORM_RECONCILER_WORKERS","{\"supervisor2\":\"executor\"}"));
    }
    @Test void workerRoleCannotActAsClusterReconciler() { invalid(environment(Map.of("executor2",target()))); }
    @Test void twoEnvironmentsCannotShareTheSameNamespaceUid() {
        var prod=target();prod.put("environment","prod");prod.put("workerId","executor2");
        invalid(environment(Map.of("supervisor",target(),"supervisor2",prod)));
    }
    @Test void workerIdentityCannotBeSharedByDifferentClusterReconcilers() { invalid(environment(Map.of("supervisor",target(),"supervisor2",target()))); }
}
