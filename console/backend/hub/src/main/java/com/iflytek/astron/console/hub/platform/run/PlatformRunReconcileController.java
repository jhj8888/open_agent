package com.iflytek.astron.console.hub.platform.run;

import jakarta.validation.Valid;
import jakarta.validation.constraints.*;
import java.security.Principal;
import java.util.List;
import java.util.UUID;
import org.springframework.web.bind.annotation.*;

/** Separate service role. Neither a worker key nor a user JWT may submit physical cleanup proof. */
@RestController
@RequestMapping("/internal/platform/reconcile")
public class PlatformRunReconcileController {
    private final RunSupervision supervision;
    public PlatformRunReconcileController(RunSupervision supervision) { this.supervision = supervision; }
    public record ClaimInput(@NotNull @Valid PlatformRunInternalController.ScopeInput scope, @NotNull UUID bootId) {}
    public record ScanInput(UUID after, @Min(1) @Max(100) int limit) {}
    public record Observation(@NotBlank @Pattern(regexp="[a-f0-9]{32}") String invocationId) {}
    public record SandboxObservation(@NotNull UUID sandboxId) {}

    @PostMapping("/runs/{runId}/claim")
    public RunSupervision.Claim claim(@PathVariable UUID runId, @RequestBody @Valid ClaimInput input, Principal principal) {
        return supervision.claim(input.scope().scope(), runId.toString(), principal.getName(), input.bootId().toString());
    }
    @PostMapping("/pending")
    public List<RunSupervision.Snapshot> pending(@RequestBody @Valid ScanInput input, Principal principal) {
        return supervision.pending(principal.getName(), input.after() == null ? null : input.after().toString(), input.limit());
    }
    @PostMapping("/attempts/{leaseId}/inspect")
    public RunSupervision.Snapshot inspect(@PathVariable UUID leaseId, Principal principal) {
        return supervision.inspect(leaseId.toString(), principal.getName());
    }
    @PostMapping("/attempts/{leaseId}/begin")
    public RunSupervision.Snapshot begin(@PathVariable UUID leaseId, Principal principal) {
        return supervision.begin(leaseId.toString(), principal.getName());
    }
    @PostMapping("/attempts/{leaseId}/observed")
    public RunSupervision.Snapshot observed(@PathVariable UUID leaseId, @RequestBody @Valid Observation input, Principal principal) {
        return supervision.observed(leaseId.toString(), principal.getName(), input.invocationId());
    }
    @PostMapping("/attempts/{leaseId}/fence")
    public RunSupervision.Snapshot fence(@PathVariable UUID leaseId, Principal principal) {
        return supervision.fence(leaseId.toString(), principal.getName());
    }
    @PostMapping("/attempts/{leaseId}/sandbox-observed")
    public RunSupervision.Snapshot sandbox(@PathVariable UUID leaseId, @RequestBody @Valid SandboxObservation input, Principal principal) {
        return supervision.adoptSandbox(leaseId.toString(), principal.getName(), input.sandboxId().toString());
    }
    @PostMapping("/attempts/{leaseId}/release")
    public RunSupervision.Snapshot release(@PathVariable UUID leaseId, @RequestBody RunSupervision.Proof proof, Principal principal) {
        return supervision.release(leaseId.toString(), principal.getName(), proof);
    }
}
