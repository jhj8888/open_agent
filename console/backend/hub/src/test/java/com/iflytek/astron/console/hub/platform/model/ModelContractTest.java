package com.iflytek.astron.console.hub.platform.model;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

import java.net.ServerSocket;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.file.Path;
import java.time.Duration;
import java.util.List;
import java.util.concurrent.atomic.AtomicInteger;
import org.junit.jupiter.api.*;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import org.springframework.ai.chat.prompt.Prompt;
import org.springframework.ai.openai.OpenAiChatModel;
import org.springframework.ai.openai.OpenAiChatOptions;
import org.springframework.ai.openai.api.OpenAiApi;
import org.springframework.ai.tool.function.FunctionToolCallback;
import org.springframework.retry.support.RetryTemplate;

/** Same Python HTTP/SSE fixture as the DeepAgents adapter suite; no real model. */
class ModelContractTest {
    static Process fixture;
    static String endpoint;
    static ModelEndpointPolicy policy;

    @BeforeAll static void startFixture() throws Exception {
        String python=System.getenv("MODEL_CONTRACT_PYTHON");
        Assumptions.assumeTrue(python!=null && !python.isBlank(), "Set MODEL_CONTRACT_PYTHON to run shared TCP contracts");
        int port;
        try(var socket=new ServerSocket(0)) { port=socket.getLocalPort(); }
        Path server=Path.of("../../../tests/model_contract/server.py").toAbsolutePath().normalize();
        var process=new ProcessBuilder(python,server.toString());
        process.environment().put("MOCK_MODEL_PORT",String.valueOf(port));
        process.redirectErrorStream(true).redirectOutput(ProcessBuilder.Redirect.DISCARD);
        fixture=process.start();
        endpoint="http://127.0.0.1:"+port;
        policy=new ModelEndpointPolicy(List.of(endpoint),List.of(),List.of(),List.of(),true);
        var client=HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(1)).build();
        for(int i=0;i<100;i++) {
            try {
                if(client.send(HttpRequest.newBuilder(URI.create(endpoint+"/health")).timeout(Duration.ofSeconds(1)).build(),HttpResponse.BodyHandlers.discarding()).statusCode()==200) return;
            } catch(Exception ignored) { }
            if(!fixture.isAlive()) fail("Shared contract server did not start");
            Thread.sleep(100);
        }
        fail("Shared contract server readiness timed out");
    }

    @AfterAll static void stopFixture() throws Exception {
        if(fixture!=null) { fixture.destroy(); if(!fixture.waitFor(5,java.util.concurrent.TimeUnit.SECONDS)) fixture.destroyForcibly(); }
    }

    static OpenAiChatModel model(String name) {
        var transport=ModelHttpClients.create(policy.resolve(endpoint+"/v1"),1,1,null);
        var api=OpenAiApi.builder().baseUrl(endpoint).apiKey("contract-test-key")
                .restClientBuilder(transport.rest()).webClientBuilder(transport.web()).build();
        return OpenAiChatModel.builder().openAiApi(api).defaultOptions(OpenAiChatOptions.builder().model(name).streamUsage(true).build())
                .retryTemplate(RetryTemplate.builder().maxAttempts(1).build()).build();
    }

    static int requests(String model) throws Exception {
        var body=HttpClient.newHttpClient().send(HttpRequest.newBuilder(URI.create(endpoint+"/stats")).build(),HttpResponse.BodyHandlers.ofString()).body();
        return new com.fasterxml.jackson.databind.ObjectMapper().readTree(body).path("models").path(model).asInt(0);
    }

    @Test void fixedResolverConnectsToApprovedAddressWithoutLookingUpTheOriginalName() {
        var uri=URI.create(endpoint.replace("127.0.0.1","does-not-resolve.invalid"));
        var transport=ModelHttpClients.create(new ModelEndpointPolicy.Approved(uri,policy.resolve(endpoint).addresses()),1,1,null);
        var api=OpenAiApi.builder().baseUrl(uri.toString()).apiKey("contract-test-key")
                .restClientBuilder(transport.rest()).webClientBuilder(transport.web()).build();
        var model=OpenAiChatModel.builder().openAiApi(api).defaultOptions(OpenAiChatOptions.builder().model("contract-text").build())
                .retryTemplate(RetryTemplate.builder().maxAttempts(1).build()).build();
        assertEquals("Contract response",model.call(new Prompt("test")).getResult().getOutput().getText());
        assertNotNull(model.stream(new Prompt("test")).collectList().block(Duration.ofSeconds(5)));
    }

    @Test void textAndStreamHaveEquivalentContentAndUsage() {
        var model=model("contract-text");
        var text=model.call(new Prompt("test"));
        assertEquals("Contract response",text.getResult().getOutput().getText());
        assertEquals(18,text.getMetadata().getUsage().getTotalTokens());
        var chunks=model.stream(new Prompt("test")).collectList().block(Duration.ofSeconds(5));
        assertNotNull(chunks);
        assertEquals("Contract response",chunks.stream().filter(c->c.getResult()!=null).map(c->c.getResult().getOutput().getText()).filter(java.util.Objects::nonNull).reduce("",String::concat));
        assertTrue(chunks.stream().anyMatch(c->c.getMetadata().getUsage().getTotalTokens()==18));
    }

    public record Numbers(int a,int b) {}

    static ModelCapabilityProbe probe(String name, boolean tools) {
        return new ModelCapabilityProbe(ModelHttpClients.create(policy.resolve(endpoint+"/v1"),1,1,null),
                endpoint,"/v1/chat/completions","contract-test-key",name,name.contains("deepseek")?"deepseek":"openai",
                name.contains("thinking"),true,tools,7,5);
    }

    @ParameterizedTest @ValueSource(strings={"contract-probe-openai","contract-probe-deepseek","contract-probe-deepseek-thinking"})
    void capabilityProbeChecksThreeRealRequestsAndPreservesThinkingToolHistory(String name) throws Exception {
        int before=requests(name);
        var checks=new AtomicInteger();
        var result=probe(name,true).run(checks::incrementAndGet);
        assertTrue(result.ok(),()->result.toString());
        assertTrue(result.toolsVerified());
        assertTrue(result.stream());
        assertEquals(7,result.version());
        assertEquals("reported",result.usage());
        assertEquals(3,result.steps().size());
        assertEquals(4,checks.get());
        for(var step:result.steps()) {
            assertEquals("passed",step.status()); assertEquals(11,step.inputTokens()); assertEquals(7,step.outputTokens());
        }
        assertEquals(before+3,requests(name));
        String report=new com.fasterxml.jackson.databind.ObjectMapper().writeValueAsString(result);
        for(String privateText:List.of("contract-test-key","Synthetic reasoning","add_numbers","call-contract-1"))
            assertFalse(report.contains(privateText));
    }

    @Test void capabilityProbeSkipsUndeclaredToolsAndDoesNotInventMissingUsage() throws Exception {
        String name="contract-probe-no-usage";
        int before=requests(name);
        var result=probe(name,false).run(()->{});
        assertTrue(result.ok()); assertFalse(result.toolsVerified());
        assertEquals("unknown",result.usage()); assertNull(result.steps().getFirst().inputTokens());
        assertEquals(List.of("passed","skipped","skipped"),result.steps().stream().map(ModelCapabilityProbe.Step::status).toList());
        assertEquals(before+1,requests(name));
    }

    @ParameterizedTest @ValueSource(strings={"contract-probe-no-tool","contract-probe-bad-args","contract-probe-no-finish","contract-probe-oversize"})
    void probeRejectsIncompleteOrUnsafeResponsesWithoutFollowingCalls(String name) throws Exception {
        int before=requests(name);
        var result=probe(name,true).run(()->{});
        assertFalse(result.ok()); assertFalse(result.toolsVerified());
        assertEquals(java.util.Map.of("contract-probe-no-tool","MODEL_TOOL_CALL_INVALID",
                "contract-probe-bad-args","MODEL_TOOL_ARGUMENTS_INVALID","contract-probe-no-finish","MODEL_RESPONSE_INCOMPLETE",
                "contract-probe-oversize","MODEL_RESPONSE_LIMIT").get(name),result.error());
        assertEquals(before+(name.endsWith("no-tool")||name.endsWith("bad-args")?2:1),requests(name));
        assertEquals("skipped",result.steps().getLast().status());
    }

    @Test void revokedProbeCannotMakeNextCallOrReturnSuccessfulResult() throws Exception {
        String name="contract-probe-revoke";
        int before=requests(name);
        var checks=new AtomicInteger();
        assertThrows(com.iflytek.astron.console.commons.tenant.TenantAccessDeniedException.class,()->probe(name,true).run(()->{
            if(checks.incrementAndGet()==2) throw new com.iflytek.astron.console.commons.tenant.TenantAccessDeniedException();
        }));
        assertEquals(before+1,requests(name));
    }

    @Test void totalDeadlineCannotBeExtendedByContinuousChunks() throws Exception {
        String name="contract-probe-trickle";
        int before=requests(name);
        long start=System.nanoTime();
        var result=new ModelCapabilityProbe(ModelHttpClients.create(policy.resolve(endpoint+"/v1"),1,1,null),
                endpoint,"/v1/chat/completions","contract-test-key",name,"openai",false,true,true,7,1).run(()->{});
        assertFalse(result.ok()); assertEquals("MODEL_TIMEOUT",result.error());
        assertTrue(Duration.ofNanos(System.nanoTime()-start).toMillis()<2500);
        assertEquals(before+1,requests(name));
    }

    @Test void connectionServiceAuditsOnlyScopedRedactedReportAndRejectsChangedVersion() throws Exception {
        var jdbc=mock(org.springframework.jdbc.core.JdbcTemplate.class);
        var access=mock(com.iflytek.astron.console.commons.tenant.TenantAccessService.class);
        var cipher=mock(ModelCredentialCipher.class);
        var row=new java.util.HashMap<String,Object>();
        row.put("version",7L); row.put("supports_stream",true); row.put("supports_tools",true); row.put("supports_usage",true);
        row.put("adapter","deepseek"); row.put("thinking_enabled",true); row.put("endpoint",endpoint+"/v1");
        row.put("model_name","contract-probe-deepseek-thinking"); row.put("connect_timeout_seconds",1);
        row.put("read_timeout_seconds",1); row.put("total_timeout_seconds",5); row.put("credential_key_id","fixture");
        row.put("credential_nonce",new byte[12]); row.put("credential_cipher",new byte[16]);
        when(jdbc.queryForList(anyString(),eq("tenant-a"),eq("test"),eq("connection-a"))).thenReturn(List.of(row));
        when(cipher.decrypt(anyString(),any())).thenReturn("contract-test-key");
        var json=new com.fasterxml.jackson.databind.ObjectMapper();
        var service=new ModelConnectionService(jdbc,access,cipher,policy,new org.springframework.mock.env.MockEnvironment(),json);
        var servlet=new org.springframework.mock.web.MockHttpServletRequest();
        servlet.setAttribute(com.iflytek.astron.console.commons.config.JwtClaimsFilter.USER_ID_ATTRIBUTE,"operator-a");
        org.springframework.web.context.request.RequestContextHolder.setRequestAttributes(new org.springframework.web.context.request.ServletRequestAttributes(servlet));
        try {
            var result=service.probe("tenant-a","test","connection-a");
            assertTrue(result.ok());
            var report=org.mockito.ArgumentCaptor.forClass(String.class);
            verify(jdbc).update(contains("model.probe.completed"),eq("operator-a"),eq("connection-a"),report.capture(),eq("tenant-a"),eq("test"));
            assertEquals(7,json.readTree(report.getValue()).path("version").asInt());
            assertFalse(report.getValue().contains("Synthetic reasoning"));
            assertFalse(report.getValue().contains("contract-test-key"));
            clearInvocations(jdbc);
            var changed=new java.util.HashMap<>(row); changed.put("version",8L);
            when(jdbc.queryForList(anyString(),eq("tenant-a"),eq("test"),eq("connection-a")))
                    .thenReturn(List.of(row),List.of(row),List.of(changed));
            int before=requests("contract-probe-deepseek-thinking");
            assertThrows(org.springframework.dao.OptimisticLockingFailureException.class,()->service.probe("tenant-a","test","connection-a"));
            assertEquals(before+1,requests("contract-probe-deepseek-thinking"));
            verify(jdbc,never()).update(anyString(),any(Object[].class));
        } finally { org.springframework.web.context.request.RequestContextHolder.resetRequestAttributes(); }
    }

    @ParameterizedTest @ValueSource(strings={"contract-unauthorized","contract-rate-limit","contract-timeout","contract-malformed-sse","contract-disconnect","contract-redirect"})
    void probeReportsStableErrorsAndNeverRetries(String name) throws Exception {
        int before=requests(name);
        var result=probe(name,true).run(()->{});
        assertFalse(result.ok());
        assertEquals(java.util.Map.of("contract-unauthorized","MODEL_AUTH_FAILED","contract-rate-limit","MODEL_RATE_LIMITED",
                "contract-timeout","MODEL_TIMEOUT","contract-malformed-sse","MODEL_PROTOCOL_ERROR",
                "contract-disconnect","MODEL_CONNECTION_FAILED","contract-redirect","MODEL_ENDPOINT_DENIED").get(name),result.error());
        assertEquals(before+1,requests(name));
    }

    @Test void fragmentedToolCallRunsOnceAndReturnsResult() {
        var called=new AtomicInteger();
        var tool=FunctionToolCallback.builder("add_numbers",(Numbers input)->{ called.incrementAndGet(); return input.a()+input.b(); })
                .description("Add two integers").inputType(Numbers.class).build();
        var options=OpenAiChatOptions.builder().toolCallbacks(tool).build();
        var response=model("contract-tools").stream(new Prompt("Add 2 and 3",options)).collectList().block(Duration.ofSeconds(5));
        assertNotNull(response);
        assertEquals(1,called.get());
        assertTrue(response.stream().anyMatch(c->c.getResult()!=null && "5".equals(c.getResult().getOutput().getText())));
    }

    @ParameterizedTest @ValueSource(strings={"contract-unauthorized","contract-rate-limit","contract-timeout","contract-malformed-sse","contract-disconnect","contract-redirect"})
    void failedStreamsCannotBecomeSuccessfulResponses(String name) throws Exception {
        int before=requests(name);
        var error=assertThrows(Exception.class,()->model(name).stream(new Prompt("test")).collectList().block(Duration.ofSeconds(4)));
        var expected=java.util.Map.of("contract-unauthorized","MODEL_AUTH_FAILED","contract-rate-limit","MODEL_RATE_LIMITED",
                "contract-timeout","MODEL_TIMEOUT","contract-malformed-sse","MODEL_PROTOCOL_ERROR",
                "contract-disconnect","MODEL_CONNECTION_FAILED","contract-redirect","MODEL_ENDPOINT_DENIED");
        assertEquals(expected.get(name),ModelFailure.from(error).code());
        assertEquals(before+1,requests(name),"A failed request must not be replayed automatically");
    }
}
