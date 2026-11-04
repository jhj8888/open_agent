package com.iflytek.astron.platform;

import com.iflytek.astron.console.commons.response.ApiResult;
import com.iflytek.astron.console.hub.platform.PlatformStatisticsService;
import org.springframework.web.bind.annotation.*;

@RestController
public class PlatformStatisticsController {
    private final PlatformStatisticsService statistics;
    public PlatformStatisticsController(PlatformStatisticsService statistics) { this.statistics=statistics; }
    @GetMapping("/platform/tenants/{tenantId}/environments/{environment}/statistics")
    public ApiResult<?> snapshot(@PathVariable String tenantId,@PathVariable String environment,
            @RequestParam(defaultValue="24h") String window) {
        return ApiResult.success(statistics.snapshot(tenantId,environment,window));
    }
}
