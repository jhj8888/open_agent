package com.iflytek.astron.platform;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;
import java.util.List;
import org.junit.jupiter.api.Test;
import org.springframework.jdbc.core.JdbcTemplate;

class OperationsMetricsTest {
    @Test void unavailableCollectionIsNeverHealthyZeroAndLabelsRemainFinite() {
        var jdbc=mock(JdbcTemplate.class);
        when(jdbc.queryForList(anyString(),any(Object[].class))).thenReturn(List.of());
        when(jdbc.queryForObject(anyString(),eq(Double.class),any(Object[].class))).thenReturn(0.0);
        try(var metrics=new OperationsMetrics(jdbc)) {
            String before=metrics.scrape();assertTrue(before.contains("open_agent_metrics_collection_success 0.0"));
            assertTrue(before.contains("NaN"));
            metrics.collect();String good=metrics.scrape();
            assertTrue(good.contains("open_agent_metrics_collection_success 1.0"));assertFalse(good.contains("NaN"));
            when(jdbc.queryForList(anyString(),any(Object[].class))).thenThrow(new IllegalStateException("private failure"));
            metrics.collect();String failed=metrics.scrape();
            assertTrue(failed.contains("open_agent_metrics_collection_success 0.0"));assertTrue(failed.contains("NaN"));
            assertFalse(failed.contains("private failure"));assertFalse(failed.contains("tenant"));assertFalse(failed.contains("run_id"));
            assertEquals(good.lines().filter(line->!line.startsWith("#")).count(),failed.lines().filter(line->!line.startsWith("#")).count());
        }
    }
}
