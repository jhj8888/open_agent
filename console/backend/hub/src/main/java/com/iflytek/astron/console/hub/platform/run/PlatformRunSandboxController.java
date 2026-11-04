package com.iflytek.astron.console.hub.platform.run;

import jakarta.validation.Valid;
import jakarta.validation.constraints.*;
import java.security.Principal;
import java.util.Map;
import java.util.UUID;
import org.springframework.web.bind.annotation.*;

/** Private service-key transport. Workers may report destruction; they cannot release either quota. */
@RestController
@RequestMapping("/internal/platform/worker/runs/{runId}/sandbox")
public class PlatformRunSandboxController {
    private final RunSandboxJournal journal;
    public PlatformRunSandboxController(RunSandboxJournal journal) { this.journal = journal; }
    public record Reservation(@NotNull @Valid PlatformRunInternalController.LeaseInput lease,
            @NotBlank @Pattern(regexp="kata|gvisor-demo") String profile, @NotBlank @Size(max=512) String image) {}
    public record Observation(@NotNull @Valid PlatformRunInternalController.LeaseInput lease, @NotNull UUID sandboxId) {}

    @PostMapping("/reserve")
    public Map<String, Boolean> reserve(@PathVariable UUID runId, @RequestBody @Valid Reservation input, Principal principal) {
        journal.reserve(input.lease().lease(runId, principal), input.profile(), input.image());
        return Map.of("accepted", true);
    }
    @PostMapping("/creating")
    public Map<String, Boolean> creating(@PathVariable UUID runId,
            @RequestBody @Valid PlatformRunInternalController.LeaseInput input, Principal principal) {
        journal.creating(input.lease(runId, principal));
        return Map.of("accepted", true);
    }
    @PostMapping("/created")
    public Map<String, Boolean> created(@PathVariable UUID runId, @RequestBody @Valid Observation input, Principal principal) {
        journal.created(input.lease().lease(runId, principal), input.sandboxId().toString());
        return Map.of("accepted", true);
    }
    @PostMapping("/cleanup-pending")
    public Map<String, Boolean> pending(@PathVariable UUID runId, @RequestBody @Valid Observation input, Principal principal) {
        journal.cleanupPending(input.lease().lease(runId, principal), input.sandboxId().toString());
        return Map.of("accepted", true);
    }
    @PostMapping("/destruction-reported")
    public Map<String, Boolean> destroyed(@PathVariable UUID runId, @RequestBody @Valid Observation input, Principal principal) {
        journal.destructionReported(input.lease().lease(runId, principal), input.sandboxId().toString());
        return Map.of("accepted", true);
    }
}
