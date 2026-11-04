package com.iflytek.astron.console.hub.platform.run;

import jakarta.validation.Valid;
import jakarta.validation.constraints.*;
import java.security.Principal;
import java.util.UUID;
import org.springframework.http.MediaType;
import org.springframework.web.bind.annotation.*;

/** Matched exclusively by PlatformInternalSecurityConfig; never exposed through user JWT authentication. */
@RestController
@RequestMapping("/internal/platform/worker/runs/{runId}")
public class PlatformRunRuntimeController {
    private final RunRuntimeService runtime;
    public PlatformRunRuntimeController(RunRuntimeService runtime) { this.runtime=runtime; }
    public record ArtifactInput(@NotNull @Valid PlatformRunInternalController.LeaseInput lease,
            @NotNull UUID releaseId, @Min(-1) @Max(99) int fileIndex) {}

    @PostMapping("/runtime")
    public RunRuntimeService.RuntimeBundle runtime(@PathVariable UUID runId,
            @RequestBody @Valid PlatformRunInternalController.LeaseInput input, Principal principal) {
        return runtime.bundle(input.lease(runId,principal));
    }
    @PostMapping(value="/artifact",produces=MediaType.APPLICATION_OCTET_STREAM_VALUE)
    public byte[] artifact(@PathVariable UUID runId, @RequestBody @Valid ArtifactInput input, Principal principal) {
        return runtime.artifact(input.lease().lease(runId,principal),input.releaseId().toString(),input.fileIndex());
    }
}
