package com.iflytek.astron.console.hub.platform;

import static org.junit.jupiter.api.Assertions.*;
import java.time.Instant;
import org.junit.jupiter.api.Test;

class AgentPeriodStatisticsTest {
    @Test void naturalShanghaiPeriodsCrossYearMonthLeapDayAndMondayCorrectly() {
        var monday=Instant.parse("2026-11-01T16:00:00Z");
        assertEquals(monday,AgentPeriodStatistics.period("this_week",monday).start());
        assertEquals(monday,AgentPeriodStatistics.period("last_week",monday).end());
        assertEquals(Instant.parse("2026-10-31T16:00:00Z"),AgentPeriodStatistics.period("this_month",monday).start());
        assertEquals(Instant.parse("2025-11-30T16:00:00Z"),AgentPeriodStatistics.period("last_month",Instant.parse("2025-12-31T16:00:00Z")).start());
        var leap=AgentPeriodStatistics.period("last_month",Instant.parse("2024-03-01T00:00:00Z"));
        assertEquals(29*86400,leap.end().getEpochSecond()-leap.start().getEpochSecond());
        assertThrows(IllegalArgumentException.class,()->AgentPeriodStatistics.period("30d",monday));
    }
}
