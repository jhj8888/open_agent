package com.iflytek.astron.platform;

import com.iflytek.astron.console.commons.config.JwtClaimsFilter;
import com.iflytek.astron.console.hub.config.PlatformInternalSecurityConfig;
import com.iflytek.astron.console.hub.config.PlatformPublicRunSecurityConfig;
import jakarta.servlet.DispatcherType;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.util.Map;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Import;
import org.springframework.core.annotation.Order;
import org.springframework.http.ResponseEntity;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.scheduling.annotation.EnableScheduling;
import org.springframework.security.config.Customizer;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.config.annotation.web.configurers.AbstractHttpConfigurer;
import org.springframework.security.config.http.SessionCreationPolicy;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.security.oauth2.server.resource.web.authentication.BearerTokenAuthenticationFilter;
import org.springframework.security.web.SecurityFilterChain;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.filter.OncePerRequestFilter;

/**
 * Dedicated Skill platform entry point. Reuses the real platform services and SQL;
 * does not load unrelated legacy knowledge, speech, workflow or cloud connectors.
 * This package is outside HubApplication's scan, preserving the original entry point.
 */
@SpringBootApplication(excludeName = {
        "org.redisson.spring.starter.RedissonAutoConfigurationV2",
        "org.springframework.boot.autoconfigure.data.redis.RedisAutoConfiguration",
        "org.springframework.boot.autoconfigure.data.redis.RedisReactiveAutoConfiguration",
        "org.springframework.boot.autoconfigure.data.redis.RedisRepositoriesAutoConfiguration",
        "com.baomidou.mybatisplus.autoconfigure.MybatisPlusAutoConfiguration",
        "org.mybatis.spring.boot.autoconfigure.MybatisAutoConfiguration",
        "com.github.yulichang.autoconfigure.MybatisPlusJoinAutoConfiguration",
        "com.github.pagehelper.autoconfigure.PageHelperAutoConfiguration",
        "org.springframework.boot.autoconfigure.jooq.JooqAutoConfiguration"
}, scanBasePackages = {
        "com.iflytek.astron.console.hub.platform",
        "com.iflytek.astron.console.commons.tenant",
        "com.iflytek.astron.platform"
})
@Import({PlatformInternalSecurityConfig.class, PlatformPublicRunSecurityConfig.class})
@EnableScheduling
public class PlatformApplication {
    public static void main(String[] args) {
        var application = new SpringApplication(PlatformApplication.class);
        application.setDefaultProperties(Map.of("spring.config.name", "platform"));
        application.run(args);
    }

    @Bean
    @Order(1)
    SecurityFilterChain platformManagement(HttpSecurity http) throws Exception {
        return http.securityMatcher("/platform/**")
                .authorizeHttpRequests(auth -> auth.anyRequest().authenticated())
                .oauth2ResourceServer(oauth -> oauth.jwt(Customizer.withDefaults()))
                .addFilterAfter(new VerifiedSubjectFilter(), BearerTokenAuthenticationFilter.class)
                .csrf(AbstractHttpConfigurer::disable)
                .requestCache(AbstractHttpConfigurer::disable)
                .sessionManagement(session -> session.sessionCreationPolicy(SessionCreationPolicy.STATELESS))
                .formLogin(AbstractHttpConfigurer::disable)
                .httpBasic(AbstractHttpConfigurer::disable)
                .build();
    }

    @Bean
    @Order(100)
    SecurityFilterChain platformFallback(HttpSecurity http) throws Exception {
        return http.authorizeHttpRequests(auth -> auth
                        .dispatcherTypeMatchers(DispatcherType.ERROR).permitAll()
                        .requestMatchers("/health/live", "/health/ready").permitAll()
                        .anyRequest().denyAll())
                .csrf(AbstractHttpConfigurer::disable)
                .requestCache(AbstractHttpConfigurer::disable)
                .sessionManagement(session -> session.sessionCreationPolicy(SessionCreationPolicy.STATELESS))
                .formLogin(AbstractHttpConfigurer::disable)
                .httpBasic(AbstractHttpConfigurer::disable)
                .build();
    }

    /** Only the verified JWT subject is adapted to the shared tenant service context. */
    static final class VerifiedSubjectFilter extends OncePerRequestFilter {
        @Override
        protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response, FilterChain chain)
                throws ServletException, IOException {
            var authentication = SecurityContextHolder.getContext().getAuthentication();
            if (authentication == null || !authentication.isAuthenticated()
                    || !(authentication.getPrincipal() instanceof Jwt jwt)
                    || jwt.getSubject() == null || jwt.getSubject().isBlank() || jwt.getSubject().length() > 128) {
                response.sendError(401);
                return;
            }
            response.setHeader("Cache-Control", "no-store");
            request.setAttribute(JwtClaimsFilter.USER_ID_ATTRIBUTE, jwt.getSubject());
            try {
                chain.doFilter(request, response);
            } finally {
                request.removeAttribute(JwtClaimsFilter.USER_ID_ATTRIBUTE);
            }
        }
    }

    @RestController
    static final class Health {
        private final JdbcTemplate jdbc;
        Health(JdbcTemplate jdbc) { this.jdbc = jdbc; }

        @GetMapping("/health/live")
        Map<String, String> live() { return Map.of("status", "UP"); }

        @GetMapping("/health/ready")
        ResponseEntity<Map<String, String>> ready() {
            try {
                // Check the migration needed by the public API, not only TCP reachability.
                jdbc.queryForList("SELECT key_id FROM oa_deployment_credential LIMIT 0");
                jdbc.queryForList("SELECT upload_id FROM oa_run_output_upload LIMIT 0");
                jdbc.queryForList("SELECT id FROM oa_run_step LIMIT 0");
                jdbc.queryForList("SELECT adapter,thinking_enabled FROM oa_model_connection_version LIMIT 0");
                jdbc.queryForList("SELECT observation_content_bytes FROM oa_run_attempt LIMIT 0");
                jdbc.queryForList("SELECT revision,acked_revision FROM oa_observation_outbox LIMIT 0");
                jdbc.queryForList("SELECT pod_uid,termination_id,not_started_id FROM oa_run_cluster_execution LIMIT 0");
                return ResponseEntity.ok(Map.of("status", "UP"));
            } catch (RuntimeException unavailable) {
                return ResponseEntity.status(503).body(Map.of("status", "DOWN"));
            }
        }
    }
}
