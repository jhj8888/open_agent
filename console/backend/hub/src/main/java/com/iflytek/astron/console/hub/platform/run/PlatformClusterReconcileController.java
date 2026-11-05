package com.iflytek.astron.console.hub.platform.run;

import jakarta.validation.Valid;
import java.security.Principal;
import java.util.List;
import java.util.UUID;
import org.springframework.web.bind.annotation.*;

/** Same reconciler security chain; only cluster-configured identities can reach this ledger. */
@RestController
@RequestMapping("/internal/platform/reconcile/cluster")
public class PlatformClusterReconcileController {
    private final RunClusterSupervision supervision;
    public PlatformClusterReconcileController(RunClusterSupervision supervision) { this.supervision = supervision; }

    @PostMapping("/runs/{runId}/claim")
    public RunClusterSupervision.Claim claim(@PathVariable UUID runId, @RequestBody @Valid PlatformRunInternalController.ScopeInput input, Principal principal) {
        return supervision.claim(input.scope(), runId.toString(), principal.getName());
    }
    @PostMapping("/runs/{runId}/lookup")
    public RunClusterSupervision.Lookup lookup(@PathVariable UUID runId, @RequestBody @Valid PlatformRunInternalController.ScopeInput input, Principal principal) {
        return supervision.lookup(input.scope(), runId.toString(), principal.getName());
    }
    @PostMapping("/runs/{runId}/abandon")
    public RunClusterSupervision.Lookup abandon(@PathVariable UUID runId, @RequestBody @Valid PlatformRunInternalController.ScopeInput input, Principal principal) {
        return supervision.abandon(input.scope(), runId.toString(), principal.getName());
    }
    @PostMapping("/pending")
    public List<RunClusterSupervision.Snapshot> pending(@RequestBody @Valid PlatformRunReconcileController.ScanInput input, Principal principal) {
        return supervision.pending(principal.getName(), input.after() == null ? null : input.after().toString(), input.limit());
    }
    @PostMapping("/attempts/{leaseId}/inspect")
    public RunClusterSupervision.Snapshot inspect(@PathVariable UUID leaseId, Principal principal) { return supervision.inspect(leaseId.toString(), principal.getName()); }
    @PostMapping("/attempts/{leaseId}/begin")
    public RunClusterSupervision.Snapshot begin(@PathVariable UUID leaseId, Principal principal) { return supervision.begin(leaseId.toString(), principal.getName()); }
    @PostMapping("/attempts/{leaseId}/observed")
    public RunClusterSupervision.Snapshot observed(@PathVariable UUID leaseId, @RequestBody RunClusterSupervision.Observation input, Principal principal) {
        return supervision.observed(leaseId.toString(), principal.getName(), input);
    }
    @PostMapping("/attempts/{leaseId}/terminated")
    public RunClusterSupervision.Snapshot terminated(@PathVariable UUID leaseId, @RequestBody RunClusterSupervision.Termination input, Principal principal) {
        return supervision.terminated(leaseId.toString(), principal.getName(), input);
    }
    @PostMapping("/attempts/{leaseId}/fence")
    public RunClusterSupervision.Snapshot fence(@PathVariable UUID leaseId, Principal principal) { return supervision.fence(leaseId.toString(), principal.getName()); }
    @PostMapping("/attempts/{leaseId}/not-started")
    public RunClusterSupervision.Snapshot notStarted(@PathVariable UUID leaseId, @RequestBody RunClusterSupervision.NotStarted input, Principal principal) {
        return supervision.notStarted(leaseId.toString(), principal.getName(), input);
    }
    @PostMapping("/attempts/{leaseId}/creation-rejected")
    public RunClusterSupervision.Snapshot creationRejected(@PathVariable UUID leaseId, @RequestBody RunClusterSupervision.CreationRejection input, Principal principal) {
        return supervision.creationRejected(leaseId.toString(), principal.getName(), input);
    }
    @PostMapping("/attempts/{leaseId}/sandbox-observed")
    public RunClusterSupervision.Snapshot sandbox(@PathVariable UUID leaseId, @RequestBody @Valid PlatformRunReconcileController.SandboxObservation input, Principal principal) {
        return supervision.adoptSandbox(leaseId.toString(), principal.getName(), input.sandboxId().toString());
    }
    @PostMapping("/attempts/{leaseId}/release")
    public RunClusterSupervision.Snapshot release(@PathVariable UUID leaseId, @RequestBody RunClusterSupervision.Proof input, Principal principal) {
        return supervision.release(leaseId.toString(), principal.getName(), input);
    }
    public record SandboxPreparation(String image) {}
    @PostMapping("/attempts/{leaseId}/sandbox-prepare")
    public RunClusterSupervision.Snapshot prepareSandbox(@PathVariable UUID leaseId, @RequestBody SandboxPreparation input, Principal principal) {
        return supervision.prepareSandbox(leaseId.toString(),principal.getName(),input.image());
    }
    @PostMapping("/attempts/{leaseId}/sandbox-resource")
    public RunClusterSupervision.Snapshot sandboxResource(@PathVariable UUID leaseId, @RequestBody RunClusterSupervision.SandboxResource input, Principal principal) {
        return supervision.sandboxResource(leaseId.toString(),principal.getName(),input);
    }
}
