package com.iflytek.astron.console.hub.platform.run;

import jakarta.annotation.PreDestroy;
import java.util.Map;
import java.util.concurrent.*;
import java.util.concurrent.atomic.*;
import org.springframework.stereotype.Component;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

/** Bounded subscriptions. Each poll reauthorizes the captured identity against current SQL state. */
@Component
public class RunEventStreams {
    private final AgentRunService runs;
    private final ExecutorService executor=Executors.newVirtualThreadPerTaskExecutor();
    private final Semaphore slots=new Semaphore(128);
    private final ConcurrentHashMap<String,Integer> subjects=new ConcurrentHashMap<>();
    public RunEventStreams(AgentRunService runs) { this.runs=runs; }
    public SseEmitter open(DeploymentAuthorization.Caller caller,String runId,long after) {
        runs.events(caller,runId,after); // Return permission/history errors before committing an SSE response.
        if(!slots.tryAcquire()) throw new RunFailure("stream_limit");
        String subject=caller.actor();AtomicBoolean admitted=new AtomicBoolean();
        subjects.compute(subject,(key,count)->{int current=count==null?0:count;if(current<8) { admitted.set(true);return current+1; }return current;});
        if(!admitted.get()) { slots.release();throw new RunFailure("stream_limit"); }
        var emitter=new SseEmitter(65000L);AtomicBoolean closed=new AtomicBoolean();AtomicReference<Future<?>> future=new AtomicReference<>();
        Runnable close=()->{
            if(closed.compareAndSet(false,true)) {
                subjects.computeIfPresent(subject,(key,count)->count==1?null:count-1);slots.release();
                var task=future.get();if(task!=null) task.cancel(true);
            }
        };
        emitter.onCompletion(close);emitter.onTimeout(close);emitter.onError(error->close.run());
        try {
            future.set(executor.submit(()->{
                long cursor=after,until=System.nanoTime()+TimeUnit.SECONDS.toNanos(60);int ticks=0;
                try {
                    while(!closed.get()&&System.nanoTime()<until) {
                        var page=runs.events(caller,runId,cursor);
                        for(var event:page.events()) {
                            cursor=((Number)event.get("sequence_no")).longValue();
                            emitter.send(SseEmitter.event().id(Long.toString(cursor)).name((String)event.get("event_type")).data(event));
                        }
                        if(page.terminal()&&cursor>=page.last_sequence()) {
                            var terminal=runs.status(caller,runId);
                            if(java.util.Set.of("failed","timed_out").contains(terminal.get("state"))) {
                                Object code=terminal.get("error_code");
                                emitter.send(SseEmitter.event().name("error").data(Map.of("run_id",runId,
                                        "code",code==null?"execution_failed":code,"terminal",true)));
                            }
                            emitter.send(SseEmitter.event().name("stream.end").data(Map.of("run_id",runId,"last_sequence",cursor)));
                            emitter.complete();return;
                        }
                        if(cursor<page.last_sequence()) continue;
                        if(ticks++%10==0) emitter.send(SseEmitter.event().comment("heartbeat"));
                        TimeUnit.SECONDS.sleep(1);
                    }
                    emitter.complete(); // The client resumes with Last-Event-ID, never resubmits the run.
                } catch(InterruptedException interrupted) { Thread.currentThread().interrupt();emitter.complete(); }
                catch(RunFailure denied) {
                    try { emitter.send(SseEmitter.event().name("error").data(Map.of("run_id",runId,"code",denied.code()))); }
                    catch(Exception ignored) { /* Disconnected socket; execution is independent of subscription. */ }
                    emitter.complete();
                } catch(Exception unavailable) {
                    try { emitter.send(SseEmitter.event().name("error").data(Map.of("run_id",runId,"code","event_stream_unavailable"))); }
                    catch(Exception ignored) { /* No credentials or raw exception strings are sent. */ }
                    emitter.complete();
                } finally { close.run(); }
            }));
            if(closed.get()) future.get().cancel(true);
        } catch(RejectedExecutionException stopped) { close.run();throw new RunFailure("executor_unavailable"); }
        return emitter;
    }
    @PreDestroy public void stop() { executor.shutdownNow(); }
}
