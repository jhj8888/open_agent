package com.iflytek.astron.platform;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.iflytek.astron.console.commons.tenant.TenantAccessDeniedException;
import com.iflytek.astron.console.hub.platform.TenantGovernanceService;
import com.sun.net.httpserver.HttpServer;
import java.net.InetSocketAddress;
import java.time.Instant;
import java.util.Map;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;
import org.junit.jupiter.api.Test;
import org.springframework.mock.env.MockEnvironment;

class HostMetricsControllerTest {
    @Test void operationsRequireAdminAndRejectStaleOrPrivateErrorBodies() throws Exception {
        var calls=new AtomicInteger();var body=new AtomicReference<String>();
        var server=HttpServer.create(new InetSocketAddress("127.0.0.1",0),0);
        server.createContext("/api/v1/query",exchange->{
            calls.incrementAndGet();byte[] bytes=body.get().getBytes(java.nio.charset.StandardCharsets.UTF_8);
            exchange.sendResponseHeaders(200,bytes.length);try(var out=exchange.getResponseBody()){out.write(bytes);}
        });server.start();
        var governance=mock(TenantGovernanceService.class);
        var controller=new HostMetricsController(governance,new ObjectMapper(),new MockEnvironment()
            .withProperty("PLATFORM_PROMETHEUS_URL","http://127.0.0.1:"+server.getAddress().getPort()));
        try {
            assertThrows(TenantAccessDeniedException.class,controller::snapshot);assertEquals(0,calls.get());
            when(governance.isPlatformAdmin()).thenReturn(true);
            body.set(response(Instant.now().getEpochSecond()));
            var result=(Map<?,?>)controller.snapshot().data();assertEquals(true,result.get("available"));
            assertEquals(45.0,((Map<?,?>)result.get("values")).get("cpuPercent"));
            body.set(response(1));assertEquals("source_stale",((Map<?,?>)controller.snapshot().data()).get("reason"));
            body.set("{\"status\":\"error\",\"error\":\"private-server-detail\"}");
            result=(Map<?,?>)controller.snapshot().data();assertEquals(false,result.get("available"));assertFalse(result.toString().contains("private-server-detail"));
        } finally {controller.close();server.stop(0);}
    }
    private String response(long stamp) { return """
        {"status":"success","data":{"resultType":"vector","result":[
          {"metric":{"metric":"sourceTimestamp"},"value":[0,"%d"]},
          {"metric":{"metric":"nodeUp"},"value":[0,"1"]},
          {"metric":{"metric":"cpuPercent"},"value":[0,"45"]}]}}
        """.formatted(stamp); }
}
