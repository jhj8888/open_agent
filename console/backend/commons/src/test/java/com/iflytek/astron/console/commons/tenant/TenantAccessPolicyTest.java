package com.iflytek.astron.console.commons.tenant;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.util.Set;
import java.util.stream.Stream;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.Arguments;
import org.junit.jupiter.params.provider.MethodSource;

class TenantAccessPolicyTest {
    static Stream<Arguments> activeMatrix() {
        return Stream.of(
                Arguments.of(TenantRole.TENANT_ADMIN, TenantEnvironment.TEST,
                        Set.of("METADATA_READ", "RESOURCE_WRITE", "RUN_CREATE", "RUN_CANCEL", "LOG_READ",
                                "MEMBER_MANAGE", "QUOTA_MANAGE", "CREDENTIAL_MANAGE", "AUDIT_READ")),
                Arguments.of(TenantRole.DEVELOPER, TenantEnvironment.TEST,
                        Set.of("METADATA_READ", "RESOURCE_WRITE", "RUN_CREATE", "RUN_CANCEL", "LOG_READ")),
                Arguments.of(TenantRole.OPERATOR, TenantEnvironment.TEST,
                        Set.of("METADATA_READ", "RUN_CREATE", "RUN_CANCEL", "LOG_READ")),
                Arguments.of(TenantRole.VIEWER, TenantEnvironment.TEST, Set.of("METADATA_READ")),
                Arguments.of(TenantRole.TENANT_ADMIN, TenantEnvironment.PROD,
                        Set.of("METADATA_READ", "RUN_CANCEL", "LOG_READ", "MEMBER_MANAGE", "QUOTA_MANAGE",
                                "CREDENTIAL_MANAGE", "AUDIT_READ")),
                Arguments.of(TenantRole.DEVELOPER, TenantEnvironment.PROD, Set.of("METADATA_READ")),
                Arguments.of(TenantRole.OPERATOR, TenantEnvironment.PROD,
                        Set.of("METADATA_READ", "RUN_CANCEL", "LOG_READ", "DEPLOYMENT_PUBLISH")),
                Arguments.of(TenantRole.VIEWER, TenantEnvironment.PROD, Set.of("METADATA_READ")));
    }

    @ParameterizedTest
    @MethodSource("activeMatrix")
    void enforcesEveryRoleEnvironmentAndAction(TenantRole role, TenantEnvironment environment, Set<String> expected) {
        for (TenantPermission action : TenantPermission.values()) {
            assertEquals(expected.contains(action.name()), TenantAccessPolicy.allows(role, environment, "active", action),
                    () -> role + "/" + environment + "/" + action);
        }
    }

    @Test
    void suspendedTenantCannotSubmitEditPublishOrChangeCredentials() {
        for (TenantRole role : TenantRole.values()) {
            for (TenantEnvironment environment : TenantEnvironment.values()) {
                for (TenantPermission action : Set.of(TenantPermission.RUN_CREATE, TenantPermission.RESOURCE_WRITE,
                        TenantPermission.DEPLOYMENT_PUBLISH, TenantPermission.CREDENTIAL_MANAGE,
                        TenantPermission.QUOTA_MANAGE, TenantPermission.MEMBER_MANAGE)) {
                    assertFalse(TenantAccessPolicy.allows(role, environment, "suspended", action));
                }
            }
        }
        assertTrue(TenantAccessPolicy.allows(TenantRole.TENANT_ADMIN, TenantEnvironment.TEST, "suspended", TenantPermission.LOG_READ));
        assertTrue(TenantAccessPolicy.allows(TenantRole.OPERATOR, TenantEnvironment.PROD, "suspended", TenantPermission.RUN_CANCEL));
        assertFalse(TenantAccessPolicy.allows(TenantRole.DEVELOPER, TenantEnvironment.TEST, "suspended", TenantPermission.LOG_READ));
        assertFalse(TenantAccessPolicy.allows(TenantRole.TENANT_ADMIN, TenantEnvironment.TEST, "archived", TenantPermission.RUN_CANCEL));
    }

    @Test
    void rejectsUnknownStatesAndEnvironmentsWithoutFallback() {
        assertFalse(TenantAccessPolicy.allows(TenantRole.TENANT_ADMIN, TenantEnvironment.PROD, "pending", TenantPermission.METADATA_READ));
        for (String value : new String[]{null, "", "production", "TEST", "prod ", "test,prod"}) {
            assertThrows(IllegalArgumentException.class, () -> TenantEnvironment.parse(value));
        }
        assertThrows(IllegalArgumentException.class, () -> TenantRole.parse("platform_admin"));
    }
}
