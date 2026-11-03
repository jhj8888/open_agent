package com.iflytek.astron.console.hub.config.security;

import com.iflytek.astron.console.toolkit.security.SandboxRuntimeCredentialTokenProvider;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.util.List;
import org.springframework.security.authentication.BadCredentialsException;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.authority.SimpleGrantedAuthority;
import org.springframework.security.core.context.SecurityContext;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.security.web.AuthenticationEntryPoint;
import org.springframework.web.filter.OncePerRequestFilter;

/** Authenticates core services before returning a transient E2B credential. */
public class SandboxRuntimeCredentialAuthenticationFilter extends OncePerRequestFilter {

    public static final String TOKEN_HEADER = SandboxRuntimeCredentialTokenProvider.TOKEN_HEADER;
    public static final String READER_ROLE = "ROLE_SANDBOX_RUNTIME_CREDENTIAL_READER";

    private final SandboxRuntimeCredentialTokenProvider tokenProvider;
    private final AuthenticationEntryPoint authenticationEntryPoint;

    public SandboxRuntimeCredentialAuthenticationFilter(
            SandboxRuntimeCredentialTokenProvider tokenProvider,
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
        if (!tokenProvider.matches(request.getHeader(TOKEN_HEADER))) {
            authenticationEntryPoint.commence(
                    request,
                    response,
                    new BadCredentialsException("Invalid sandbox runtime credential"));
            return;
        }

        UsernamePasswordAuthenticationToken authentication =
                UsernamePasswordAuthenticationToken.authenticated(
                        "sandbox-runtime-credential-reader",
                        null,
                        List.of(new SimpleGrantedAuthority(READER_ROLE)));
        SecurityContext context = SecurityContextHolder.createEmptyContext();
        context.setAuthentication(authentication);
        SecurityContextHolder.setContext(context);
        filterChain.doFilter(request, response);
    }
}
