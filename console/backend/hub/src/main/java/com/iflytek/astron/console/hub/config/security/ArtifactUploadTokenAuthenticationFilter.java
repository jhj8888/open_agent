package com.iflytek.astron.console.hub.config.security;

import com.iflytek.astron.console.toolkit.security.ArtifactUploadTokenProvider;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.util.List;
import lombok.extern.slf4j.Slf4j;
import org.springframework.security.authentication.BadCredentialsException;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.authority.SimpleGrantedAuthority;
import org.springframework.security.core.context.SecurityContext;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.security.web.AuthenticationEntryPoint;
import org.springframework.web.filter.OncePerRequestFilter;

/** Authenticates the sandbox-to-console artifact upload using its dedicated service secret. */
@Slf4j
public class ArtifactUploadTokenAuthenticationFilter extends OncePerRequestFilter {

    public static final String TOKEN_HEADER = "X-Skill-Sandbox-Artifact-Token";
    public static final String UPLOADER_ROLE = "ROLE_ARTIFACT_UPLOADER";

    private final ArtifactUploadTokenProvider tokenProvider;
    private final AuthenticationEntryPoint authenticationEntryPoint;

    public ArtifactUploadTokenAuthenticationFilter(
            ArtifactUploadTokenProvider tokenProvider,
            AuthenticationEntryPoint authenticationEntryPoint) {
        this.tokenProvider = tokenProvider;
        this.authenticationEntryPoint = authenticationEntryPoint;
    }

    @Override
    protected void doFilterInternal(
            HttpServletRequest request,
            HttpServletResponse response,
            FilterChain filterChain)
            throws ServletException, IOException {
        String token = request.getHeader(TOKEN_HEADER);
        if (!tokenProvider.matches(token)) {
            log.debug("Rejected workflow artifact upload authentication from {}", request.getRemoteAddr());
            authenticationEntryPoint.commence(
                    request, response, new BadCredentialsException("Invalid artifact upload credential"));
            return;
        }

        UsernamePasswordAuthenticationToken authentication =
                UsernamePasswordAuthenticationToken.authenticated(
                        "workflow-artifact-uploader",
                        null,
                        List.of(new SimpleGrantedAuthority(UPLOADER_ROLE)));
        SecurityContext context = SecurityContextHolder.createEmptyContext();
        context.setAuthentication(authentication);
        SecurityContextHolder.setContext(context);
        filterChain.doFilter(request, response);
    }
}
