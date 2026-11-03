package com.iflytek.astron.console.toolkit.entity.dto.skill;

import lombok.AllArgsConstructor;
import lombok.Data;

/** Secret response returned only by the authenticated private credential broker. */
@Data
@AllArgsConstructor
public class SkillSandboxRuntimeCredentialDto {
    private String provider;
    private String apiKey;
    private Integer timeoutSeconds;
    private Boolean allowInternetAccess;
}
