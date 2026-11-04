package com.iflytek.astron.console.hub.platform.run;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.mock.env.MockEnvironment;
import org.springframework.transaction.PlatformTransactionManager;

class RunSupervisionConfigurationTest {
    private final JdbcTemplate jdbc=mock(JdbcTemplate.class);
    private RunSupervision instance(String bindings,String keys) {
        return new RunSupervision(mock(RunLedger.class),mock(RunSandboxJournal.class),jdbc,
                mock(PlatformTransactionManager.class),new ObjectMapper(),new MockEnvironment()
                .withProperty("PLATFORM_RECONCILER_WORKERS",bindings).withProperty("PLATFORM_SERVICE_KEYS",keys));
    }
    private String keys(String role,String workerSecret) {
        return "{\"supervisor\":{\"role\":\"reconciler\",\"secret\":\""+"s".repeat(32)
                +"\"},\"executor\":{\"role\":\""+role+"\",\"secret\":\""+workerSecret+"\"}}";
    }
    @Test void unconfiguredSupervisorFailsClosedBeforeDatabaseAccess() {
        var service=instance("{}","{}");
        assertEquals("action_forbidden",assertThrows(RunFailure.class,()->service.pending("unbound",null,10)).code());
        verifyNoInteractions(jdbc);
    }
    @Test void missingWrongRoleAndSharedCredentialsAreRejectedWithoutEchoingConfiguration() {
        for(String keys:java.util.List.of("{}",keys("dispatcher","w".repeat(32)),keys("worker","s".repeat(32)))) {
            var error=assertThrows(IllegalStateException.class,()->instance("{\"supervisor\":\"executor\"}",keys));
            assertEquals("Invalid platform supervisor bindings",error.getMessage());
            assertNull(error.getCause());
        }
        verifyNoInteractions(jdbc);
    }
    @Test void boundSupervisorRejectsMalformedOrUnboundedScanBeforeStorage() {
        var service=instance("{\"supervisor\":\"executor\"}",keys("worker","w".repeat(32)));
        assertEquals("invalid_cursor",assertThrows(RunFailure.class,()->service.pending("supervisor",null,101)).code());
        assertEquals("invalid_identifier",assertThrows(RunFailure.class,()->service.pending("supervisor","not-a-lease",10)).code());
        verifyNoInteractions(jdbc);
    }
}
