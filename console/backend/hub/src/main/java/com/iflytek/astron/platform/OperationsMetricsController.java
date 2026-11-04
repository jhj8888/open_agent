package com.iflytek.astron.platform;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class OperationsMetricsController {
    private final OperationsMetrics metrics;
    public OperationsMetricsController(OperationsMetrics metrics) { this.metrics=metrics; }
    @GetMapping(value="/internal/platform/metrics",produces="text/plain;version=0.0.4;charset=utf-8")
    public String scrape() { return metrics.scrape(); }
}
