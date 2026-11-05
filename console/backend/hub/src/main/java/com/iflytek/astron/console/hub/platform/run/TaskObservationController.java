package com.iflytek.astron.console.hub.platform.run;

import com.iflytek.astron.console.commons.response.ApiResult;
import jakarta.validation.Valid;
import java.security.Principal;
import java.util.*;
import org.springframework.web.bind.annotation.*;

@RestController
public class TaskObservationController {
    private final TaskObservationService service;
    public TaskObservationController(TaskObservationService service) {this.service=service;}
    public record Input(@Valid PlatformRunInternalController.LeaseInput lease,String phase,TaskObservationService.Step step) {}
    @PostMapping("/internal/platform/worker/runs/{runId}/observations")
    public Map<String,Object> record(@PathVariable UUID runId,@RequestBody @Valid Input input,Principal principal) {
        if(input.lease()==null)throw new RunFailure("invalid_observation");
        return service.record(input.lease().lease(runId,principal),input.phase(),input.step());
    }
    @GetMapping("/api/v1/runs/{runId}/observations")
    public ApiResult<Map<String,Object>> list(@PathVariable UUID runId,@RequestParam(defaultValue="0") long after,@RequestParam(defaultValue="100") int limit) {
        return ApiResult.success(service.list(DeploymentAuthorization.current(),runId.toString(),after,limit));
    }
}
