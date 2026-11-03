package com.iflytek.astron.console.hub.platform;

import com.iflytek.astron.console.commons.response.ApiResult;
import jakarta.validation.Valid;
import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;
import java.util.List;
import java.util.Map;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/platform/tenants")
public class PlatformTenantController {
    private final TenantGovernanceService service;

    public PlatformTenantController(TenantGovernanceService service) {
        this.service = service;
    }

    public record CreateTenant(@NotBlank @Size(max = 50) String displayName, @NotBlank @Size(max = 128) String ownerUid) {}
    public record StatusUpdate(@NotBlank @Pattern(regexp = "active|suspended|archived") String status, @Min(0) long revision) {}
    public record QuotaUpdate(@Min(0) @Max(100000) int maxConcurrentRuns, @Min(0) @Max(1000000) int maxQueuedRuns,
            @Min(0) @Max(100000) int maxSandboxes, @Min(0) long dailyTokenBudget,
            @Min(1) @Max(3650) int auditRetentionDays, @Min(0) long revision) {}
    public record MemberUpdate(@NotBlank @Pattern(regexp = "tenant_admin|developer|operator|viewer") String role, @Min(0) Long revision) {}

    @GetMapping
    public ApiResult<?> list() {
        return ApiResult.success(Map.of("platformAdmin", service.isPlatformAdmin(), "tenants", service.tenants()));
    }

    @PostMapping
    public ApiResult<?> create(@RequestBody @Valid CreateTenant request) {
        return ApiResult.success(Map.of("tenantId", service.createTenant(request.displayName().trim(), request.ownerUid())));
    }

    @GetMapping("/{tenantId}/memberships")
    public ApiResult<List<Map<String, Object>>> memberships(@PathVariable String tenantId) {
        return ApiResult.success(service.memberships(tenantId));
    }

    @PutMapping("/{tenantId}/status")
    public ApiResult<?> status(@PathVariable String tenantId, @RequestBody @Valid StatusUpdate request) {
        service.setStatus(tenantId, request.status(), request.revision());
        return ApiResult.success();
    }

    @GetMapping("/{tenantId}/environments/{environment}/quotas")
    public ApiResult<?> quotas(@PathVariable String tenantId, @PathVariable String environment) {
        return ApiResult.success(service.quotas(tenantId, environment));
    }

    @PutMapping("/{tenantId}/environments/{environment}/quotas")
    public ApiResult<?> quotas(@PathVariable String tenantId, @PathVariable String environment, @RequestBody @Valid QuotaUpdate request) {
        service.setQuotas(tenantId, environment, request);
        return ApiResult.success();
    }

    @GetMapping("/{tenantId}/environments/{environment}/members")
    public ApiResult<?> members(@PathVariable String tenantId, @PathVariable String environment) {
        return ApiResult.success(service.members(tenantId, environment));
    }

    @PutMapping("/{tenantId}/environments/{environment}/members/{uid}")
    public ApiResult<?> member(@PathVariable String tenantId, @PathVariable String environment,
            @PathVariable String uid, @RequestBody @Valid MemberUpdate request) {
        service.setMember(tenantId, environment, uid, request.role(), request.revision(), false);
        return ApiResult.success();
    }

    @PutMapping("/{tenantId}/environments/{environment}/administrators/{uid}")
    public ApiResult<?> administrator(@PathVariable String tenantId, @PathVariable String environment,
            @PathVariable String uid, @RequestBody @Valid MemberUpdate request) {
        service.setMember(tenantId, environment, uid, request.role(), request.revision(), true);
        return ApiResult.success();
    }

    @DeleteMapping("/{tenantId}/environments/{environment}/members/{uid}")
    public ApiResult<?> removeMember(@PathVariable String tenantId, @PathVariable String environment,
            @PathVariable String uid, @RequestParam long revision) {
        service.removeMember(tenantId, environment, uid, revision);
        return ApiResult.success();
    }

    @GetMapping("/{tenantId}/environments/{environment}/audit")
    public ApiResult<?> audit(@PathVariable String tenantId, @PathVariable String environment,
            @RequestParam(defaultValue = "9223372036854775807") long beforeId) {
        return ApiResult.success(service.auditEntries(tenantId, environment, beforeId));
    }
}
