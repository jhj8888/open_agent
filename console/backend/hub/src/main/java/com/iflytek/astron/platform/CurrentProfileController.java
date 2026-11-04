package com.iflytek.astron.platform;

import com.iflytek.astron.console.commons.response.ApiResult;
import com.iflytek.astron.console.hub.platform.CurrentProfileService;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RestController;

/** Standalone platform only; caller-supplied identity and roles are never read. */
@RestController
public class CurrentProfileController {
    private final CurrentProfileService profiles;
    public CurrentProfileController(CurrentProfileService profiles) { this.profiles = profiles; }

    @PostMapping("/platform/profile")
    public ApiResult<?> register(@AuthenticationPrincipal Jwt jwt) {
        Object name = jwt.getClaims().get("name");
        return ApiResult.success(profiles.register(jwt.getSubject(), name instanceof String text ? text : null));
    }
}
