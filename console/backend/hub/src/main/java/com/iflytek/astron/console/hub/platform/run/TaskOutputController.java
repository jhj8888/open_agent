package com.iflytek.astron.console.hub.platform.run;

import com.iflytek.astron.console.commons.response.ApiResult;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.Valid;
import java.security.Principal;
import java.nio.charset.StandardCharsets;
import java.util.*;
import org.springframework.core.io.Resource;
import org.springframework.http.*;
import org.springframework.web.bind.annotation.*;

@RestController
public class TaskOutputController {
    private final TaskOutputService outputs;
    public TaskOutputController(TaskOutputService outputs) {this.outputs=outputs;}
    public record Reserve(@Valid PlatformRunInternalController.LeaseInput lease,String path,long size,String sha256,String step) {}
    @PostMapping("/internal/platform/worker/runs/{runId}/outputs")
    public Map<String,Object> reserve(@PathVariable UUID runId,@RequestBody @Valid Reserve input,Principal principal) {
        if(input.lease()==null)throw new RunFailure("invalid_output");
        return outputs.reserve(input.lease().lease(runId,principal),new TaskOutputService.Descriptor(input.path(),input.size(),input.sha256(),input.step()));
    }
    @PostMapping(value="/internal/platform/worker/runs/{runId}/outputs/{id}/content",consumes=MediaType.APPLICATION_OCTET_STREAM_VALUE)
    public Map<String,Object> upload(@PathVariable UUID runId,@PathVariable UUID id,
            @RequestHeader("X-Output-Tenant") String tenant,@RequestHeader("X-Output-Environment") String environment,
            @RequestHeader("X-Output-Attempt") int attempt,@RequestHeader("X-Output-Lease") UUID lease,
            @RequestHeader("X-Output-Fence") long fence,Principal principal,HttpServletRequest request) throws java.io.IOException {
        return outputs.upload(new RunLedger.Lease(new RunLedger.Scope(tenant,environment),runId.toString(),attempt,lease.toString(),fence,principal.getName()),
                id.toString(),request.getInputStream(),request.getContentLengthLong());
    }
    @GetMapping("/api/v1/runs/{runId}/outputs")
    public ApiResult<List<Map<String,Object>>> list(@PathVariable UUID runId) {return ApiResult.success(outputs.list(DeploymentAuthorization.current(),runId.toString()));}
    @GetMapping("/api/v1/runs/{runId}/outputs/{id}/download")
    public ResponseEntity<Resource> download(@PathVariable UUID runId,@PathVariable UUID id) {
        var file=outputs.download(DeploymentAuthorization.current(),runId.toString(),id.toString(),false);
        return ResponseEntity.ok().contentType(MediaType.APPLICATION_OCTET_STREAM).contentLength(file.size())
                .header(HttpHeaders.CONTENT_DISPOSITION,ContentDisposition.attachment().filename(file.name(),StandardCharsets.UTF_8).build().toString())
                .header("X-Content-Type-Options","nosniff").header("Content-Security-Policy","sandbox; default-src 'none'")
                .header("X-Content-SHA256",file.sha256()).cacheControl(CacheControl.noStore()).body(file.content());
    }
    @GetMapping("/api/v1/runs/{runId}/outputs/{id}/preview")
    public ApiResult<Map<String,String>> preview(@PathVariable UUID runId,@PathVariable UUID id) throws java.io.IOException {
        var file=outputs.download(DeploymentAuthorization.current(),runId.toString(),id.toString(),true);
        try(var input=file.content().getInputStream()) {return ApiResult.success(Map.of("text",new String(input.readNBytes(256*1024),StandardCharsets.UTF_8)));}
    }
}
