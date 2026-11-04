package com.iflytek.astron.console.hub.platform.run;

import com.fasterxml.jackson.databind.JsonNode;
import jakarta.validation.Valid;
import jakarta.validation.constraints.*;
import java.security.Principal;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import org.springframework.web.bind.annotation.*;

/** Service-only transport. Physical worker identity always comes from authentication, never a body field. */
@RestController
@RequestMapping("/internal/platform")
public class PlatformRunInternalController {
    private final RunLedger ledger;
    private final RunOutbox outbox;
    public PlatformRunInternalController(RunLedger ledger, RunOutbox outbox) { this.ledger = ledger; this.outbox = outbox; }

    public record ScopeInput(@NotBlank @Pattern(regexp="[A-Za-z0-9_-]{1,80}") String tenant,
            @NotBlank @Pattern(regexp="test|prod") String environment) {
        RunLedger.Scope scope() { return new RunLedger.Scope(tenant, environment); }
    }
    public record LeaseInput(@NotBlank @Pattern(regexp="[A-Za-z0-9_-]{1,80}") String tenant,
            @NotBlank @Pattern(regexp="test|prod") String environment, @Min(1) @Max(100) int attempt,
            @NotNull UUID leaseId, @Min(1) long fencingToken) {
        RunLedger.Lease lease(UUID run, Principal principal) {
            return new RunLedger.Lease(new RunLedger.Scope(tenant, environment), run.toString(), attempt,
                    leaseId.toString(), fencingToken, principal.getName());
        }
    }
    public record ClaimInput(@Min(1) @Max(100) int limit, @Min(1) @Max(60) int leaseSeconds) {}
    public record AckInput(@NotNull UUID claimId, @Min(0) @Max(300) int delaySeconds) {}
    public record EventInput(@NotNull @Valid LeaseInput lease, @NotBlank @Size(max=120) String eventKey,
            @NotBlank @Size(max=32) String eventType, @NotNull JsonNode payload) {
        @Override public String toString() { return "EventInput[payload=REDACTED]"; }
    }
    public record FinishInput(@NotNull @Valid LeaseInput lease,
            @NotBlank @Pattern(regexp="succeeded|failed|timed_out") String state, @Size(max=64) String errorCode) {}

    @PostMapping("/dispatch/claim")
    public List<RunOutbox.Delivery> deliveries(@RequestBody @Valid ClaimInput input) { return outbox.claim(input.limit(), input.leaseSeconds()); }
    @PostMapping("/dispatch/{id}/ack")
    public Map<String, Boolean> ack(@PathVariable long id, @RequestBody @Valid AckInput input) {
        return Map.of("accepted", outbox.delivered(delivery(id, input)));
    }
    @PostMapping("/dispatch/{id}/retry")
    public Map<String, Boolean> retry(@PathVariable long id, @RequestBody @Valid AckInput input) {
        return Map.of("accepted", outbox.retryLater(delivery(id, input), input.delaySeconds()));
    }
    private RunOutbox.Delivery delivery(long id, AckInput input) {
        if (id < 1) throw new RunFailure("invalid_dispatch_identifier");
        return new RunOutbox.Delivery(id, null, null, null, null, input.claimId().toString(), null, 0);
    }

    @PostMapping({"/dispatch/runs/{runId}/status", "/worker/runs/{runId}/status"})
    public Map<String, Object> status(@PathVariable UUID runId, @RequestBody @Valid ScopeInput input) {
        return ledger.get(input.scope(), runId.toString());
    }
    @PostMapping("/worker/runs/{runId}/claim")
    public Map<String, Object> claim(@PathVariable UUID runId, @RequestBody @Valid ScopeInput input, Principal principal) {
        var lease = ledger.claim(input.scope(), runId.toString(), principal.getName());
        Map<String, Object> result = new HashMap<>();
        result.put("lease", lease.orElse(null));
        return result;
    }
    @PostMapping("/worker/runs/{runId}/heartbeat")
    public Map<String, Boolean> heartbeat(@PathVariable UUID runId, @RequestBody @Valid LeaseInput input, Principal principal) {
        return Map.of("permitted", ledger.heartbeat(input.lease(runId, principal)));
    }
    @PostMapping("/worker/runs/{runId}/permit")
    public Map<String, Boolean> permit(@PathVariable UUID runId, @RequestBody @Valid LeaseInput input, Principal principal) {
        return ledger.withPermit(input.lease(runId, principal), () -> Map.of("permitted", true));
    }
    @PostMapping("/worker/runs/{runId}/events")
    public Map<String, Long> event(@PathVariable UUID runId, @RequestBody @Valid EventInput input, Principal principal) {
        return Map.of("sequence", ledger.append(input.lease().lease(runId, principal), input.eventKey(), input.eventType(), input.payload()));
    }
    @PostMapping("/worker/runs/{runId}/finish")
    public Map<String, Object> finish(@PathVariable UUID runId, @RequestBody @Valid FinishInput input, Principal principal) {
        return ledger.finish(input.lease().lease(runId, principal), RunState.parse(input.state()), input.errorCode());
    }
}
