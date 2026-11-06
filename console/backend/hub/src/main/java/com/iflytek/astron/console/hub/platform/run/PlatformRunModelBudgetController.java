package com.iflytek.astron.console.hub.platform.run;

import jakarta.validation.Valid;
import jakarta.validation.constraints.*;
import java.security.Principal;
import java.util.Map;
import java.util.UUID;
import org.springframework.web.bind.annotation.*;

/** Private authenticated worker routes. No public enablement or caller-declared bound. */
@RestController
@RequestMapping("/internal/platform/worker/runs/{runId}/model-calls")
public class PlatformRunModelBudgetController {
    private final RunModelBudgetService service;
    public PlatformRunModelBudgetController(RunModelBudgetService service) {this.service=service;}
    public record Call(@NotNull @Valid PlatformRunInternalController.LeaseInput lease,@NotNull UUID callId,
                       @NotBlank @Pattern(regexp="[a-f0-9]{64}") String requestSha256) {}
    public record Settlement(@NotNull @Valid Call call,@NotNull @Min(0) @Max(1000000000) Long inputTokens,
                             @NotNull @Min(0) @Max(1000000000) Long outputTokens) {}
    @PostMapping("/prepare") public TokenBudgetLedger.Reservation prepare(@PathVariable UUID runId,@RequestBody @Valid Call input,Principal principal) {
        return service.prepare(input.lease().lease(runId,principal),input.callId().toString(),input.requestSha256());
    }
    @PostMapping("/dispatch") public Map<String,Boolean> dispatch(@PathVariable UUID runId,@RequestBody @Valid Call input,Principal principal) {
        return Map.of("permitted",service.dispatch(input.lease().lease(runId,principal),input.callId().toString(),input.requestSha256()));
    }
    @PostMapping("/cancel") public Map<String,Boolean> cancel(@PathVariable UUID runId,@RequestBody @Valid Call input,Principal principal) {
        return Map.of("cancelled",service.cancelPrepared(input.lease().lease(runId,principal),input.callId().toString(),input.requestSha256()));
    }
    @PostMapping("/unknown") public TokenBudgetLedger.Reservation unknown(@PathVariable UUID runId,@RequestBody @Valid Call input,Principal principal) {
        return service.unknown(input.lease().lease(runId,principal),input.callId().toString(),input.requestSha256());
    }
    @PostMapping("/settle") public TokenBudgetLedger.Reservation settle(@PathVariable UUID runId,@RequestBody @Valid Settlement input,Principal principal) {
        var call=input.call();
        return service.settle(call.lease().lease(runId,principal),call.callId().toString(),call.requestSha256(),input.inputTokens(),input.outputTokens());
    }
}
