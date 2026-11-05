package com.iflytek.astron.console.hub.platform.run;

import jakarta.validation.Valid;
import jakarta.validation.constraints.*;
import java.security.Principal;
import java.util.*;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/internal/platform/trace-export")
public class ObservationExportController {
    private final ObservationOutbox outbox;
    public ObservationExportController(ObservationOutbox outbox){this.outbox=outbox;}
    public record ClaimInput(@Min(30) @Max(300) int leaseSeconds) {}
    public record Input(@NotNull UUID claimId,@Min(1) long revision,@Min(0) long after,
            @Min(0) @Max(300) int delaySeconds,@Size(max=40) String error) {}
    @PostMapping("/claim")
    public List<ObservationOutbox.Claim> claim(@RequestBody @Valid ClaimInput input,Principal principal){
        return outbox.claim(principal.getName(),input.leaseSeconds());
    }
    @PostMapping("/{id}/page")
    public Map<String,Object> page(@PathVariable long id,@RequestBody @Valid Input input,Principal principal){
        return outbox.page(id,principal.getName(),input.claimId().toString(),input.revision(),input.after());
    }
    @PostMapping("/{id}/ack")
    public Map<String,Boolean> ack(@PathVariable long id,@RequestBody @Valid Input input,Principal principal){
        return Map.of("accepted",outbox.acknowledge(id,principal.getName(),input.claimId().toString(),input.revision()));
    }
    @PostMapping("/{id}/retry")
    public Map<String,Boolean> retry(@PathVariable long id,@RequestBody @Valid Input input,Principal principal){
        return Map.of("accepted",outbox.retry(id,principal.getName(),input.claimId().toString(),input.revision(),input.delaySeconds(),input.error()));
    }
}
