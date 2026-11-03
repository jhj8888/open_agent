package com.iflytek.astron.console.hub.platform.model;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.iflytek.astron.console.commons.tenant.TenantAccessDeniedException;
import com.iflytek.astron.console.commons.tenant.TenantAccessService;
import com.iflytek.astron.console.commons.tenant.TenantPermission;
import java.net.InetAddress;
import java.util.Base64;
import java.util.List;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.mock.env.MockEnvironment;

class ModelSecurityTest {
    @ParameterizedTest
    @ValueSource(strings={"http://user:secret@llm.local/v1", "file:///etc/passwd", "http://llm.local:0/v1", "http://llm.local./v1", "http://llm.local/v1?token=secret", "http://llm.local/#secret", "http://[fe80::1%25eth0]/v1"})
    void malformedEndpointsAreRejectedWithoutEcho(String value) {
        Exception error=assertThrows(IllegalArgumentException.class, () -> ModelEndpointPolicy.parse(value));
        assertFalse(error.getMessage().contains(value));
    }

    @ParameterizedTest
    @ValueSource(strings={"127.0.0.1", "::1", "0.0.0.0", "169.254.169.254", "100.100.100.200", "168.63.129.16", "224.0.0.1", "8.8.8.8", "10.9.0.1"})
    void unapprovedAddressesAreRejected(String value) throws Exception {
        var policy = new ModelEndpointPolicy(List.of("http://llm.local"), List.of(), List.of("10.0.0.0/8"), List.of("10.9.0.1"), false);
        var address=InetAddress.getByName(value);
        assertThrows(IllegalArgumentException.class, () -> policy.validateAddress(address, "http://llm.local:80"));
    }

    @Test void defaultConfigurationDoesNotAllowPublicFallback() {
        var policy=new ModelEndpointPolicy(new MockEnvironment(), new ObjectMapper());
        assertThrows(IllegalArgumentException.class, () -> policy.validate("https://api.deepseek.com/v1"));
    }

    @Test void ciphertextCannotBeMovedBetweenTenantsEnvironmentsOrVersions() {
        var environment=new MockEnvironment().withProperty("MODEL_CREDENTIAL_KEYS", "{\"v1\":\"" + Base64.getEncoder().encodeToString(new byte[32]) + "\"}");
        var cipher=new ModelCredentialCipher(environment, new ObjectMapper());
        String scope=ModelConnectionService.scope("tenant-a","test","model-a",1);
        var encrypted=cipher.encrypt(scope,"synthetic-private-key");
        assertEquals("synthetic-private-key",cipher.decrypt(scope,encrypted));
        assertFalse(new String(encrypted.ciphertext(), java.nio.charset.StandardCharsets.UTF_8).contains("synthetic-private-key"));
        for(String other:List.of(ModelConnectionService.scope("tenant-b","test","model-a",1), ModelConnectionService.scope("tenant-a","prod","model-a",1), ModelConnectionService.scope("tenant-a","test","model-a",2))) {
            assertThrows(IllegalStateException.class,()->cipher.decrypt(other,encrypted));
        }
        encrypted.ciphertext()[0]^=1;
        assertThrows(IllegalStateException.class,()->cipher.decrypt(scope,encrypted));
    }

    @Test void revokedEnvironmentAccessCannotReadModelConfigurationOrCredentials() {
        var access=mock(TenantAccessService.class);
        var jdbc=mock(JdbcTemplate.class);
        var cipher=mock(ModelCredentialCipher.class);
        var policy=mock(ModelEndpointPolicy.class);
        var service=new ModelConnectionService(jdbc,access,cipher,policy,new MockEnvironment(),new ObjectMapper());
        when(access.require("tenant-b","prod",TenantPermission.METADATA_READ)).thenThrow(new TenantAccessDeniedException());
        when(access.require("tenant-b","prod",TenantPermission.RUN_CREATE)).thenThrow(new TenantAccessDeniedException());
        assertThrows(TenantAccessDeniedException.class,()->service.list("tenant-b","prod"));
        assertThrows(TenantAccessDeniedException.class,()->service.forRun("tenant-b","prod","guessed-id",true,true));
        verifyNoInteractions(jdbc,cipher,policy);
    }

    @Test void inputSerializationAndToStringNeverReturnTheKey() throws Exception {
        var input=new PlatformModelController.ModelInput("example","http://llm.local/v1","model",true,true,true,10,90,180,null,"synthetic-private-key",0,false);
        assertFalse(input.toString().contains("synthetic-private-key"));
        assertFalse(new ObjectMapper().writeValueAsString(input).contains("synthetic-private-key"));
    }
}
