package com.iflytek.astron.console.hub.platform.run;

import java.util.Locale;
import java.util.Set;

public enum RunState {
    QUEUED, RUNNING, CANCEL_REQUESTED, SUCCEEDED, FAILED, TIMED_OUT, CANCELLED;

    public String value() { return name().toLowerCase(Locale.ROOT); }
    public static RunState parse(String value) { return valueOf(value.toUpperCase(Locale.ROOT)); }
    public boolean terminal() { return Set.of(SUCCEEDED, FAILED, TIMED_OUT, CANCELLED).contains(this); }

    public boolean canTransitionTo(RunState next) {
        if (next == this) return true;
        return switch (this) {
            case QUEUED -> Set.of(RUNNING, CANCEL_REQUESTED, TIMED_OUT, FAILED).contains(next);
            case RUNNING -> Set.of(SUCCEEDED, FAILED, TIMED_OUT, CANCEL_REQUESTED).contains(next);
            case CANCEL_REQUESTED -> next == CANCELLED;
            default -> false;
        };
    }
}
