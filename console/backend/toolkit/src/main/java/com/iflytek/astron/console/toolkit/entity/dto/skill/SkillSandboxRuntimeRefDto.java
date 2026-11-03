package com.iflytek.astron.console.toolkit.entity.dto.skill;

import lombok.Data;

/** Non-secret sandbox reference safe to serialize into workflow and agent protocols. */
@Data
public class SkillSandboxRuntimeRefDto {
    private String provider;
    private Boolean enabled;
    private String uid;
    private Long spaceId;
}
