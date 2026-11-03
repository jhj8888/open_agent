package com.iflytek.astron.console.hub.platform;

import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verifyNoMoreInteractions;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.put;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.iflytek.astron.console.commons.config.JwtClaimsFilter;
import com.iflytek.astron.console.commons.tenant.TenantAccessDeniedException;
import com.iflytek.astron.console.commons.tenant.TenantAccessService;
import com.iflytek.astron.console.commons.tenant.TenantPermission;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.http.MediaType;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;
import org.springframework.web.context.request.RequestContextHolder;
import org.springframework.web.context.request.ServletRequestAttributes;

class TenantGovernanceSecurityTest {
    private final JdbcTemplate jdbc = mock(JdbcTemplate.class);
    private final TenantAccessService access = mock(TenantAccessService.class);
    private final TenantGovernanceService service = new TenantGovernanceService(jdbc, access, new ObjectMapper());

    @BeforeEach
    void authenticate() {
        MockHttpServletRequest request = new MockHttpServletRequest();
        request.setAttribute(JwtClaimsFilter.USER_ID_ATTRIBUTE, "developer");
        RequestContextHolder.setRequestAttributes(new ServletRequestAttributes(request));
    }

    @AfterEach
    void cleanup() {
        RequestContextHolder.resetRequestAttributes();
    }

    @Test
    void tenantCreationAndSuspensionRequirePlatformAdministration() {
        when(jdbc.queryForObject(anyString(), eq(Boolean.class), eq("developer"))).thenReturn(false);
        assertThrows(TenantAccessDeniedException.class, () -> service.createTenant("Forged", "developer"));
        assertThrows(TenantAccessDeniedException.class, () -> service.setStatus("space-1", "suspended", 0));
        org.mockito.Mockito.verify(jdbc, org.mockito.Mockito.times(2)).queryForObject(anyString(), eq(Boolean.class), eq("developer"));
        verifyNoMoreInteractions(jdbc);
    }

    @Test
    void ordinaryMemberCannotPromoteSelfOrBootstrapProduction() {
        when(jdbc.queryForList(anyString(), eq("space-1"))).thenReturn(List.of(Map.of("status", "active", "space_id", 1L)));
        doThrow(new TenantAccessDeniedException()).when(access).require("space-1", "test", TenantPermission.MEMBER_MANAGE);
        when(jdbc.queryForObject(anyString(), eq(Boolean.class), eq("developer"))).thenReturn(false);
        assertThrows(TenantAccessDeniedException.class,
                () -> service.setMember("space-1", "test", "developer", "tenant_admin", 0L, false));
        assertThrows(TenantAccessDeniedException.class,
                () -> service.setMember("space-1", "prod", "developer", "tenant_admin", null, true));
        org.mockito.Mockito.verify(jdbc, org.mockito.Mockito.times(2)).queryForList(anyString(), eq("space-1"));
        org.mockito.Mockito.verify(jdbc).queryForObject(anyString(), eq(Boolean.class), eq("developer"));
        verifyNoMoreInteractions(jdbc);
    }

    @Test
    void unauthorizedQuotaChangeDoesNotMutateOrAudit() {
        when(jdbc.queryForList(anyString(), eq("space-2"))).thenReturn(List.of(Map.of("status", "active")));
        doThrow(new TenantAccessDeniedException()).when(access).require("space-2", "prod", TenantPermission.QUOTA_MANAGE);
        assertThrows(TenantAccessDeniedException.class, () -> service.setQuotas("space-2", "prod",
                new PlatformTenantController.QuotaUpdate(100, 100, 100, 1000, 180, 0)));
        org.mockito.Mockito.verify(jdbc).queryForList(anyString(), eq("space-2"));
        verifyNoMoreInteractions(jdbc);
    }

    @Test
    void controllerRejectsNegativeQuotasBeforeCallingService() throws Exception {
        TenantGovernanceService mocked = mock(TenantGovernanceService.class);
        MockMvc mvc = MockMvcBuilders.standaloneSetup(new PlatformTenantController(mocked))
                .setControllerAdvice(new PlatformExceptionHandler()).build();
        mvc.perform(put("/platform/tenants/space-1/environments/test/quotas")
                .contentType(MediaType.APPLICATION_JSON)
                .content("{\"maxConcurrentRuns\":-1,\"maxQueuedRuns\":1,\"maxSandboxes\":1,\"dailyTokenBudget\":1,\"auditRetentionDays\":180,\"revision\":0}"))
                .andExpect(status().isBadRequest());
        verifyNoMoreInteractions(mocked);
    }

    @Test
    void deniedResourceReturns404WithoutDistinguishingExistence() throws Exception {
        TenantGovernanceService mocked = mock(TenantGovernanceService.class);
        doThrow(new TenantAccessDeniedException()).when(mocked).setStatus(anyString(), anyString(), eq(0L));
        MockMvc mvc = MockMvcBuilders.standaloneSetup(new PlatformTenantController(mocked))
                .setControllerAdvice(new PlatformExceptionHandler()).build();
        mvc.perform(put("/platform/tenants/unknown/status").contentType(MediaType.APPLICATION_JSON)
                .content("{\"status\":\"suspended\",\"revision\":0}"))
                .andExpect(status().isNotFound());
    }
}
