package com.iflytek.astron.console.hub.config;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.List;
import java.util.Map;
import java.util.Set;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.core.annotation.Order;
import org.springframework.core.env.Environment;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.config.annotation.web.configurers.AbstractHttpConfigurer;
import org.springframework.security.config.http.SessionCreationPolicy;
import org.springframework.security.core.authority.SimpleGrantedAuthority;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.security.web.SecurityFilterChain;
import org.springframework.security.web.authentication.AnonymousAuthenticationFilter;
import org.springframework.web.filter.OncePerRequestFilter;

/** Dedicated service identities; user JWTs and legacy shared workflow keys cannot enter this chain. */
@Configuration
public class PlatformInternalSecurityConfig {
    public record ServiceKey(String role, String secret) {
        @Override public String toString() { return "ServiceKey[REDACTED]"; }
    }

    @Bean
    @Order(0)
    public SecurityFilterChain platformInternalChain(HttpSecurity http, Environment environment, ObjectMapper json) throws Exception {
        Map<String, ServiceKey> keys;
        try {
            keys = json.readValue(environment.getProperty("PLATFORM_SERVICE_KEYS", "{}"), new TypeReference<>() {});
            if (keys == null || keys.size() > 100) throw new IllegalArgumentException();
            var secrets = new java.util.HashSet<String>();
            for (var entry : keys.entrySet()) {
                var key = entry.getValue();
                if (!entry.getKey().matches("[A-Za-z0-9_.:-]{1,128}") || key == null || key.role() == null
                        || !Set.of("worker", "dispatcher", "reconciler", "observer").contains(key.role()) || key.secret() == null
                        || !key.secret().matches("[!-~]{32,256}") || !secrets.add(key.secret())) throw new IllegalArgumentException();
            }
            keys = Map.copyOf(keys);
        } catch (Exception invalid) { throw new IllegalStateException("Invalid platform service identity configuration"); }
        var filter = new ServiceIdentityFilter(keys);
        return http.securityMatcher("/internal/platform/**")
                .authorizeHttpRequests(auth -> auth
                        .requestMatchers(org.springframework.http.HttpMethod.GET,"/internal/platform/metrics").hasAuthority("PLATFORM_OBSERVER")
                        .requestMatchers("/internal/platform/dispatch/**").hasAuthority("PLATFORM_DISPATCHER")
                        .requestMatchers("/internal/platform/worker/**").hasAuthority("PLATFORM_WORKER")
                        .requestMatchers("/internal/platform/reconcile/**").hasAuthority("PLATFORM_RECONCILER")
                        .anyRequest().denyAll())
                .csrf(AbstractHttpConfigurer::disable)
                .requestCache(AbstractHttpConfigurer::disable)
                .sessionManagement(session -> session.sessionCreationPolicy(SessionCreationPolicy.STATELESS))
                .formLogin(AbstractHttpConfigurer::disable).httpBasic(AbstractHttpConfigurer::disable)
                .exceptionHandling(errors -> errors.authenticationEntryPoint((request, response, exception) -> response.sendError(401))
                        .accessDeniedHandler((request, response, exception) -> response.sendError(403)))
                .addFilterBefore(filter, AnonymousAuthenticationFilter.class).build();
    }

    static final class ServiceIdentityFilter extends OncePerRequestFilter {
        private final Map<String, ServiceKey> keys;
        ServiceIdentityFilter(Map<String, ServiceKey> keys) { this.keys = keys; }
        @Override protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response, FilterChain chain)
                throws ServletException, IOException {
            response.setHeader("Cache-Control", "no-store");
            response.setHeader("Pragma", "no-cache");
            String id = request.getHeader("X-Platform-Key-Id"), supplied = request.getHeader("X-Platform-Service-Key");
            ServiceKey key = id == null ? null : keys.get(id);
            if (key == null || supplied == null || supplied.length() > 256
                    || !MessageDigest.isEqual(key.secret().getBytes(StandardCharsets.UTF_8), supplied.getBytes(StandardCharsets.UTF_8))) {
                response.sendError(401); return;
            }
            var context = SecurityContextHolder.createEmptyContext();
            context.setAuthentication(UsernamePasswordAuthenticationToken.authenticated(id, null,
                    List.of(new SimpleGrantedAuthority("PLATFORM_" + key.role().toUpperCase(java.util.Locale.ROOT)))));
            SecurityContextHolder.setContext(context);
            chain.doFilter(request, response);
        }
    }
}
