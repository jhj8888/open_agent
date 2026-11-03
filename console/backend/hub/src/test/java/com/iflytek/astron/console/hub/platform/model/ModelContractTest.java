package com.iflytek.astron.console.hub.platform.model;

import static org.junit.jupiter.api.Assertions.*;

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
