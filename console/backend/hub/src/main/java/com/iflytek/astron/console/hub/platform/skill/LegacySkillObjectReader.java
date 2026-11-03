package com.iflytek.astron.console.hub.platform.skill;

/** Only server-resolved draft keys are accepted; never a user-supplied URL. */
public interface LegacySkillObjectReader {
    byte[] read(String expectedScope,String objectKey);
}
