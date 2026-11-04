package com.iflytek.astron.console.hub.platform.run;

import com.iflytek.astron.console.commons.response.ApiResult;
import jakarta.validation.Valid;
import java.net.URI;
import java.util.UUID;
import org.springframework.http.*;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

@RestController
@RequestMapping("/api/v1")
public class AgentRunController {
    private final AgentRunService runs;
    private final RunEventStreams streams;
    public AgentRunController(AgentRunService runs,RunEventStreams streams) { this.runs=runs;this.streams=streams; }
    @GetMapping("/agent-deployments/{id}") public ApiResult<?> deployment(@PathVariable UUID id) {
        return ApiResult.success(runs.deployment(DeploymentAuthorization.current(),id.toString()));
    }
    @GetMapping("/agent-deployments/{id}/runs") public ApiResult<?> list(@PathVariable UUID id) {
        return ApiResult.success(runs.list(DeploymentAuthorization.current(),id.toString()));
    }
    @PostMapping("/agent-deployments/{id}/runs") public ResponseEntity<?> submit(@PathVariable UUID id,
            @RequestHeader(value="Idempotency-Key",required=false) String key,@RequestBody @Valid AgentRunService.Submit input) {
        var result=runs.submit(DeploymentAuthorization.current(),id.toString(),key,input);
        return ResponseEntity.accepted().location(URI.create(result.status_url())).cacheControl(CacheControl.noStore()).body(ApiResult.success(result));
    }
    @GetMapping("/runs/{id}") public ApiResult<?> status(@PathVariable UUID id) {
        return ApiResult.success(runs.status(DeploymentAuthorization.current(),id.toString()));
    }
    @PostMapping("/runs/{id}/cancel") public ApiResult<?> cancel(@PathVariable UUID id) {
        return ApiResult.success(runs.cancel(DeploymentAuthorization.current(),id.toString()));
    }
    @GetMapping(value="/runs/{id}/events",produces=MediaType.APPLICATION_JSON_VALUE)
    public ApiResult<?> events(@PathVariable UUID id,@RequestParam(defaultValue="0") long after) {
        return ApiResult.success(runs.events(DeploymentAuthorization.current(),id.toString(),after));
    }
    @GetMapping(value="/runs/{id}/events",produces=MediaType.TEXT_EVENT_STREAM_VALUE)
    public ResponseEntity<SseEmitter> stream(@PathVariable UUID id,@RequestParam(defaultValue="0") long after,
            @RequestHeader(value="Last-Event-ID",required=false) String last) {
        long cursor=after;
        if(last!=null) {
            if(!last.matches("[0-9]{1,18}")) throw new RunFailure("invalid_cursor");cursor=Long.parseLong(last);
        }
        return ResponseEntity.ok().cacheControl(CacheControl.noStore()).header("X-Accel-Buffering","no")
                .body(streams.open(DeploymentAuthorization.current(),id.toString(),cursor));
    }
}
