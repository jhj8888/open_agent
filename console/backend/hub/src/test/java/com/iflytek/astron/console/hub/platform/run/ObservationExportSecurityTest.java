package com.iflytek.astron.console.hub.platform.run;

import static org.mockito.Mockito.*;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.*;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;

import com.iflytek.astron.console.hub.config.PlatformInternalSecurityConfig;
import com.iflytek.astron.console.hub.platform.PlatformExceptionHandler;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.boot.test.mock.mockito.MockBean;
import org.springframework.context.annotation.Import;
import org.springframework.test.web.servlet.MockMvc;

@WebMvcTest(controllers=ObservationExportController.class,properties={
    "PLATFORM_SERVICE_KEYS={\"exporter\":{\"role\":\"trace_exporter\",\"secret\":\"eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee\"},\"worker\":{\"role\":\"worker\",\"secret\":\"wwwwwwwwwwwwwwwwwwwwwwwwwwwwwwww\"}}"
})
@Import({PlatformInternalSecurityConfig.class,PlatformExceptionHandler.class})
class ObservationExportSecurityTest {
    @Autowired MockMvc mvc;
    @MockBean ObservationOutbox outbox;
    @MockBean com.iflytek.astron.console.commons.data.UserInfoDataService users;
    @Test void onlyDedicatedExporterMayClaimAndCannotExecuteOrReadRuntimeSecrets() throws Exception {
        String path="/internal/platform/trace-export/claim";
        mvc.perform(post(path).header("Authorization","Bearer tenant-user").contentType("application/json").content("{\"leaseSeconds\":300}"))
                .andExpect(status().isUnauthorized());
        mvc.perform(post(path).header("X-Platform-Key-Id","worker").header("X-Platform-Service-Key","w".repeat(32))
                .contentType("application/json").content("{\"leaseSeconds\":300}")).andExpect(status().isForbidden());
        for(String route:java.util.List.of("/internal/platform/worker/runs/example/runtime","/internal/platform/dispatch/claim","/internal/platform/reconcile/claim"))
            mvc.perform(post(route).header("X-Platform-Key-Id","exporter").header("X-Platform-Service-Key","e".repeat(32)))
                    .andExpect(status().isForbidden());
        verifyNoInteractions(outbox);
        mvc.perform(post(path).header("X-Platform-Key-Id","exporter").header("X-Platform-Service-Key","e".repeat(32))
                .contentType("application/json").content("{\"leaseSeconds\":300,\"owner\":\"forged\"}"))
                .andExpect(status().isOk()).andExpect(header().string("Cache-Control","no-store"));
        verify(outbox).claim("exporter",300);
    }
}
