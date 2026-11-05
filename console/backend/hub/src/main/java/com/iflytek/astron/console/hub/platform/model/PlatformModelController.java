package com.iflytek.astron.console.hub.platform.model;

import com.fasterxml.jackson.annotation.JsonProperty;
import com.iflytek.astron.console.commons.response.ApiResult;
import jakarta.validation.Valid;
import jakarta.validation.constraints.*;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/platform/tenants/{tenantId}/environments/{environment}/models")
public class PlatformModelController {
    private final ModelConnectionService service;
    public PlatformModelController(ModelConnectionService service) { this.service = service; }

    public record ModelInput(@NotBlank @Size(max=120) String displayName, @NotBlank @Size(max=2048) String endpoint,
            @NotBlank @Size(max=255) String modelName, boolean supportsStream, boolean supportsTools, boolean supportsUsage,
            @Min(1) @Max(60) int connectTimeoutSeconds, @Min(1) @Max(1800) int readTimeoutSeconds,
            @Min(1) @Max(3600) int totalTimeoutSeconds, @Size(max=128) String caRef,
            @JsonProperty(access=JsonProperty.Access.WRITE_ONLY) @Size(max=4096) String apiKey,
            @Min(0) long expectedVersion, boolean revokePrevious,
            @Pattern(regexp="openai|deepseek") String adapter, boolean thinkingEnabled) {
        @Override public String toString() { return "ModelInput[credential=REDACTED]"; }
    }

    @GetMapping public ApiResult<?> list(@PathVariable String tenantId, @PathVariable String environment) {
        return ApiResult.success(service.list(tenantId, environment));
    }
    @PostMapping public ApiResult<?> create(@PathVariable String tenantId, @PathVariable String environment, @RequestBody @Valid ModelInput input) {
        return ApiResult.success(service.save(tenantId, environment, null, input));
    }
    @PutMapping("/{connectionId}") public ApiResult<?> update(@PathVariable String tenantId, @PathVariable String environment,
            @PathVariable String connectionId, @RequestBody @Valid ModelInput input) {
        return ApiResult.success(service.save(tenantId, environment, connectionId, input));
    }
    @PostMapping("/{connectionId}/probe") public ApiResult<?> probe(@PathVariable String tenantId, @PathVariable String environment,
            @PathVariable String connectionId) {
        return ApiResult.success(service.probe(tenantId, environment, connectionId));
    }
}
