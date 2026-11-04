package com.iflytek.astron.console.hub.platform.run;

/** Stable codes; never include prompts, credentials or raw worker exceptions. */
public final class RunFailure extends RuntimeException {
    private final String code;
    public RunFailure(String code) { super(code); this.code = code; }
    public String code() { return code; }
}
