package com.iflytek.astron.platform;

import com.iflytek.astron.console.commons.response.ApiResult;
import com.iflytek.astron.console.hub.platform.AgentPeriodStatistics;
import org.springframework.web.bind.annotation.*;

@RestController
public class AgentPeriodStatisticsController {
    private final AgentPeriodStatistics service;
    public AgentPeriodStatisticsController(AgentPeriodStatistics service) {this.service=service;}
    @GetMapping("/platform/tenants/{tenantId}/environments/{environment}/observation-statistics")
    public ApiResult<?> snapshot(@PathVariable String tenantId,@PathVariable String environment,
            @RequestParam(defaultValue="this_week") String period) {
        return ApiResult.success(service.snapshot(tenantId,environment,period));
    }
}
