package com.iflytek.astron.console.commons.tenant;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

import com.iflytek.astron.console.commons.config.JwtClaimsFilter;
import com.iflytek.astron.console.commons.exception.BusinessException;
import java.util.Optional;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.web.context.request.RequestContextHolder;
import org.springframework.web.context.request.ServletRequestAttributes;

class TenantAccessServiceTest {
    private final TenantAccessRepository repository = mock(TenantAccessRepository.class);
    private final TenantAccessService service = new TenantAccessService(repository);

    @AfterEach
    void cleanup() {
        RequestContextHolder.resetRequestAttributes();
    }

    private MockHttpServletRequest authenticate(String uid) {
        MockHttpServletRequest request = new MockHttpServletRequest();
        request.setAttribute(JwtClaimsFilter.USER_ID_ATTRIBUTE, uid);
        RequestContextHolder.setRequestAttributes(new ServletRequestAttributes(request));
        return request;
    }

    @Test
    void readsIdentityFromVerifiedJwtAttributeAndIgnoresForgedHeaders() {
        MockHttpServletRequest request = authenticate("alice");
        request.addHeader("X-User-Id", "root");
        request.addHeader("X-Tenant-Role", "tenant_admin");
        request.addHeader("X-Environment", "prod");
        TenantContext context = new TenantContext("space-1", TenantEnvironment.TEST, "alice",
                TenantRole.DEVELOPER, "active", 2, 3);
        when(repository.findContext("alice", "space-1", TenantEnvironment.TEST)).thenReturn(Optional.of(context));
        assertEquals(context, service.require("space-1", "test", TenantPermission.RESOURCE_WRITE));
        verify(repository).findContext("alice", "space-1", TenantEnvironment.TEST);
        assertThrows(TenantAccessDeniedException.class, () -> service.require("space-1", "test", TenantPermission.MEMBER_MANAGE));
    }

    @Test
    void crossTenantCrossEnvironmentAndMissingResourceHaveIdenticalErrors() {
        authenticate("alice");
        when(repository.findContext("alice", "space-2", TenantEnvironment.TEST)).thenReturn(Optional.empty());
        when(repository.findContext("alice", "space-1", TenantEnvironment.PROD)).thenReturn(Optional.empty());
        when(repository.findContext("alice", "missing", TenantEnvironment.TEST)).thenReturn(Optional.empty());
        String expected = "Tenant resource is unavailable";
        assertEquals(expected, assertThrows(TenantAccessDeniedException.class,
                () -> service.require("space-2", "test", TenantPermission.METADATA_READ)).getMessage());
        assertEquals(expected, assertThrows(TenantAccessDeniedException.class,
                () -> service.require("space-1", "prod", TenantPermission.LOG_READ)).getMessage());
        assertEquals(expected, assertThrows(TenantAccessDeniedException.class,
                () -> service.require("missing", "test", TenantPermission.METADATA_READ)).getMessage());
    }

    @Test
    void rawIdentityHeaderCannotAuthenticate() {
        MockHttpServletRequest request = authenticate(null);
        request.addHeader("X-User-Id", "alice");
        assertThrows(BusinessException.class, () -> service.require("space-1", "test", TenantPermission.METADATA_READ));
        verifyNoInteractions(repository);
    }

    @Test
    void missingEnvironmentCannotSilentlySelectAnotherScope() {
        authenticate("alice");
        assertThrows(TenantAccessDeniedException.class, () -> service.require("space-1", null, TenantPermission.METADATA_READ));
        verifyNoInteractions(repository);
    }

    @Test
    void rechecksDatabaseMembershipAfterRoleRevocation() {
        authenticate("alice");
        TenantContext context = new TenantContext("space-1", TenantEnvironment.TEST, "alice",
                TenantRole.DEVELOPER, "active", 0, 0);
        when(repository.findContext("alice", "space-1", TenantEnvironment.TEST))
                .thenReturn(Optional.of(context), Optional.empty());
        service.require("space-1", "test", TenantPermission.RUN_CREATE);
        assertThrows(TenantAccessDeniedException.class, () -> service.require("space-1", "test", TenantPermission.RUN_CREATE));
    }
}
