package com.iflytek.astron.console.hub.platform.model;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.annotation.JsonAppend;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.concurrent.atomic.AtomicLong;
import org.springframework.ai.deepseek.api.DeepSeekApi;
import org.springframework.ai.deepseek.api.DeepSeekApi.*;
import org.springframework.ai.deepseek.api.DeepSeekApi.ChatCompletionMessage.Role;
import org.springframework.core.io.buffer.DataBufferUtils;
import org.springframework.http.codec.json.Jackson2JsonEncoder;

/** Bounded diagnostic, not an Agent executor. Only the fixed pure 2 + 3 tool is allowed.
 * Uses the official wire SDK because ChatModel 1.1.2 discards DeepSeek reasoning on
 * tool history. HTTP/SSE and fragmented tool decoding remain in the official SDK. */
public final class ModelCapabilityProbe {
    static final int MAX_BYTES = 1024 * 1024;
    private static final ObjectMapper JSON = new ObjectMapper()
            .enable(com.fasterxml.jackson.core.JsonParser.Feature.STRICT_DUPLICATE_DETECTION)
            .enable(com.fasterxml.jackson.databind.DeserializationFeature.FAIL_ON_TRAILING_TOKENS);
    @JsonAppend(attrs = {@JsonAppend.Attr("thinking"), @JsonAppend.Attr("stream_options")})
    abstract static class RequestExtras {}

    public record Step(String name, String status, long durationMs, Integer inputTokens,
            Integer outputTokens, String error) {}
    public record Result(boolean ok, long version, long durationMs, boolean stream, boolean toolsVerified,
            String usage, String adapter, boolean thinkingEnabled, String error, List<Step> steps) {}
    private final DeepSeekApi api;
    private final String model;
    private final boolean tools;
    private final String adapter;
    private final boolean thinking;
    private final long version;
    private final int seconds;

    public ModelCapabilityProbe(ModelHttpClients transport, String origin, String path, String key,
            String model, String adapter, boolean thinking, boolean usage, boolean tools, long version, int seconds) {
        this.model=model; this.adapter=adapter; this.thinking=thinking; this.tools=tools;
        this.version=version; this.seconds=Math.min(seconds,60);
        // SDK 1.1.2 lacks these two request fields. Jackson's official append extension
        // adds only configured values; no custom SSE parser or HTTP implementation.
        var mapper=JSON.copy().addMixIn(ChatCompletionRequest.class,RequestExtras.class);
        var config=mapper.getSerializationConfig();
        if ("deepseek".equals(adapter)) config=config.withAttribute("thinking",Map.of("type",thinking?"enabled":"disabled"));
        if (usage) config=config.withAttribute("stream_options",Map.of("include_usage",true));
        mapper.setConfig(config);
        var web=transport.web().codecs(c->{c.defaultCodecs().jackson2JsonEncoder(new Jackson2JsonEncoder(mapper));
            c.defaultCodecs().maxInMemorySize(64*1024);});
        web.filter((request,next)->next.exchange(request).map(response->{
            var bytes=new AtomicLong();
            return response.mutate().body(body->body.handle((buffer,sink)->{
                if(bytes.addAndGet(buffer.readableByteCount())>MAX_BYTES) {
                    DataBufferUtils.release(buffer); sink.error(new ProbeFailure("MODEL_RESPONSE_LIMIT"));
                } else sink.next(buffer);
            })).build();
        }));
        api=DeepSeekApi.builder().baseUrl(origin).completionsPath(path).apiKey(key)
                .restClientBuilder(transport.rest()).webClientBuilder(web).build();
    }

    public Result run(Runnable authorized) {
        long started=System.nanoTime(), deadline=started+Duration.ofSeconds(seconds).toNanos();
        List<Step> steps=new ArrayList<>();
        boolean stream=false, verified=false;
        String failure=null;
        List<ChatCompletionMessage> history=new ArrayList<>();
        for(String stage:List.of("stream","tool_call","tool_result")) {
            if(failure!=null || (!tools&&!stage.equals("stream"))) {
                steps.add(new Step(stage,"skipped",0,null,null,null)); continue;
            }
            authorized.run(); // Do not swallow revocation/version errors as upstream failures.
            long stepStarted=System.nanoTime();
            var answer=new Answer();
            try {
                if(stage.equals("stream")) history= new ArrayList<>(List.of(new ChatCompletionMessage(
                        "Reply with OK. This is a platform connection test. Do not call tools.",Role.USER)));
                if(stage.equals("tool_call")) history=new ArrayList<>(List.of(new ChatCompletionMessage(
                        "Call add_numbers exactly once with a=2 and b=3. After receiving its result, reply with only the number 5. Do not call any other tools.",Role.USER)));
                var functions=stage.equals("stream")?null:List.of(new FunctionTool(new FunctionTool.Function(
                        "Add the two supplied integers", "add_numbers",
                        "{\"type\":\"object\",\"properties\":{\"a\":{\"type\":\"integer\"},\"b\":{\"type\":\"integer\"}},\"required\":[\"a\",\"b\"],\"additionalProperties\":false}")));
                var request=new ChatCompletionRequest(history,model,null,1024,null,null,null,true,null,null,null,null,
                        functions,functions==null?null:"auto");
                long remaining=deadline-System.nanoTime();
                if(remaining<=0) throw new ProbeFailure("MODEL_TIMEOUT");
                // Timeout wraps completion, not each chunk: a slow trickle cannot extend it.
                api.chatCompletionStream(request).doOnNext(answer::accept).then()
                        .timeout(Duration.ofNanos(remaining)).block();
                if(stage.equals("tool_call")) {
                    if(answer.finish!=ChatCompletionFinishReason.TOOL_CALLS || answer.calls.size()!=1)
                        throw new ProbeFailure("MODEL_TOOL_CALL_INVALID");
                    var call=answer.calls.getFirst();
                    if(call.id()==null || call.id().isBlank() || call.id().length()>256 || !"function".equals(call.type())
                            || call.function()==null || !"add_numbers".equals(call.function().name()))
                        throw new ProbeFailure("MODEL_TOOL_CALL_INVALID");
                    var args=JSON.readTree(call.function().arguments());
                    if(args==null || !args.isObject() || args.size()!=2 || !args.path("a").isIntegralNumber()
                            || !args.path("b").isIntegralNumber() || !args.path("a").canConvertToInt()
                            || !args.path("b").canConvertToInt() || args.path("a").intValue()!=2 || args.path("b").intValue()!=3)
                        throw new ProbeFailure("MODEL_TOOL_ARGUMENTS_INVALID");
                    history.add(new ChatCompletionMessage(answer.text.toString(),Role.ASSISTANT,null,null,
                            answer.calls,null,thinking?answer.reasoning.toString():null));
                    history.add(new ChatCompletionMessage("5",Role.TOOL,null,call.id(),null));
                } else {
                    if(answer.finish!=ChatCompletionFinishReason.STOP || !answer.calls.isEmpty() || answer.text.toString().isBlank())
                        throw new ProbeFailure("MODEL_RESPONSE_INCOMPLETE");
                    if(stage.equals("tool_result") && !answer.text.toString().strip().equals("5"))
                        throw new ProbeFailure("MODEL_TOOL_RESULT_INVALID");
                    if(stage.equals("stream")) stream=true; else verified=true;
                }
                steps.add(answer.step(stage,"passed",stepStarted,null));
            } catch(Exception error) {
                failure=ModelFailure.from(error).code();
                for(Throwable cause=error;cause!=null;cause=cause.getCause())
                    if(cause instanceof ProbeFailure f) { failure=f.code; break; }
                steps.add(answer.step(stage,"failed",stepStarted,failure));
            }
        }
        authorized.run();
        boolean usage=steps.stream().filter(s->!s.status().equals("skipped"))
                .allMatch(s->s.inputTokens()!=null&&s.outputTokens()!=null);
        return new Result(failure==null,version,elapsed(started),stream,verified,usage?"reported":"unknown",
                adapter,thinking,failure,List.copyOf(steps));
    }

    static long elapsed(long start) { return (System.nanoTime()-start)/1_000_000; }
    static final class ProbeFailure extends RuntimeException {
        final String code;
        ProbeFailure(String code) { super(code); this.code=code; }
    }
    private static final class Answer {
        final StringBuilder text=new StringBuilder(), reasoning=new StringBuilder();
        final List<ChatCompletionMessage.ToolCall> calls=new ArrayList<>();
        ChatCompletionFinishReason finish;
        Usage usage;
        int chunks;
        void accept(ChatCompletionChunk chunk) {
            if(++chunks>8192) throw new ProbeFailure("MODEL_RESPONSE_LIMIT");
            if(chunk.usage()!=null) usage=chunk.usage();
            if(chunk.choices()==null) throw new ProbeFailure("MODEL_PROTOCOL_ERROR");
            for(var choice:chunk.choices()) {
                if(choice.index()==null || choice.index()!=0) throw new ProbeFailure("MODEL_PROTOCOL_ERROR");
                if(choice.finishReason()!=null) finish=choice.finishReason();
                var delta=choice.delta();
                if(delta==null) continue;
                if(delta.content()!=null) text.append(delta.content());
                if(delta.reasoningContent()!=null) reasoning.append(delta.reasoningContent());
                if(delta.toolCalls()!=null) calls.addAll(delta.toolCalls());
                if(calls.size()>1 || text.length()+reasoning.length()>64*1024) throw new ProbeFailure("MODEL_RESPONSE_LIMIT");
            }
        }
        Step step(String name,String status,long start,String error) {
            Integer input=usage==null?null:usage.promptTokens(), output=usage==null?null:usage.completionTokens();
            if(input!=null&&input<0) input=null;
            if(output!=null&&output<0) output=null;
            return new Step(name,status,elapsed(start),input,output,error);
        }
    }
}
