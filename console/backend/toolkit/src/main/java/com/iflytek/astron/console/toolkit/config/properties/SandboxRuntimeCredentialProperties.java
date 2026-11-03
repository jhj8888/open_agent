package com.iflytek.astron.console.toolkit.config.properties;

import jakarta.validation.constraints.AssertTrue;
import lombok.Getter;
import lombok.Setter;
import org.apache.commons.lang3.StringUtils;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.validation.annotation.Validated;

/** Independent service credential for retrieving per-scope E2B runtime configuration. */
@Getter
@Setter
@Validated
@ConfigurationProperties(prefix = "skill.sandbox.runtime-credential")
public class SandboxRuntimeCredentialProperties {

    private String token;
    private String tokenFile;

    @AssertTrue(message = "skill.sandbox runtime credential token or token file must be configured")
    public boolean isTokenSourceConfigured() {
        return StringUtils.isNotBlank(token) || StringUtils.isNotBlank(tokenFile);
    }
}
