package com.iflytek.astron.console.hub.config;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.iflytek.astron.console.commons.response.ApiResult;
import com.iflytek.astron.console.hub.platform.run.DeploymentKeys;
import jakarta.servlet.DispatcherType;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.*;
import java.io.IOException;
import java.util.List;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.core.annotation.Order;
import org.springframework.dao.DataAccessException;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.config.Customizer;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.config.annotation.web.configurers.AbstractHttpConfigurer;
import org.springframework.security.config.http.SessionCreationPolicy;
import org.springframework.security.core.authority.SimpleGrantedAuthority;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.security.oauth2.server.resource.web.DefaultBearerTokenResolver;
import org.springframework.security.oauth2.server.resource.web.authentication.BearerTokenAuthenticationFilter;
import org.springframework.security.web.SecurityFilterChain;
import org.springframework.web.cors.CorsConfiguration;
import org.springframework.web.filter.OncePerRequestFilter;

/** New paths only. Existing console JWT, legacy workflow and internal service chains are unchanged. */
@Configuration
public class PlatformPublicRunSecurityConfig {
    @Bean @Order(-1)
    public SecurityFilterChain platformPublicRuns(HttpSecurity http,DeploymentKeys keys,ObjectMapper json) throws Exception {
        var resolver=new DefaultBearerTokenResolver();
        return http.securityMatcher("/api/v1/agent-deployments/**","/api/v1/runs/**")
                .authorizeHttpRequests(auth->auth.dispatcherTypeMatchers(DispatcherType.ASYNC).permitAll().anyRequest().authenticated())
                .oauth2ResourceServer(oauth->oauth.jwt(Customizer.withDefaults()).bearerTokenResolver(request->{
                    String token=resolver.resolve(request);return token!=null&&token.startsWith(DeploymentKeys.PREFIX)?null:token;
                }))
                .addFilterBefore(new DeploymentKeyFilter(keys,json),BearerTokenAuthenticationFilter.class)
                .csrf(AbstractHttpConfigurer::disable).requestCache(AbstractHttpConfigurer::disable)
                .sessionManagement(session->session.sessionCreationPolicy(SessionCreationPolicy.STATELESS))
                .formLogin(AbstractHttpConfigurer::disable).httpBasic(AbstractHttpConfigurer::disable)
                .cors(cors->cors.configurationSource(request->{
                    var config=new CorsConfiguration();config.setAllowedOriginPatterns(List.of("*"));
                    config.setAllowedMethods(List.of("GET","POST","OPTIONS"));
                    config.setAllowedHeaders(List.of("Authorization","Content-Type","Accept","Idempotency-Key","Last-Event-ID"));
                    config.setAllowCredentials(false);return config;
                }))
                .exceptionHandling(errors->errors.authenticationEntryPoint((request,response,error)->failure(json,response,401,"unauthenticated"))
                        .accessDeniedHandler((request,response,error)->failure(json,response,403,"action_forbidden")))
                .build();
    }
    private static void failure(ObjectMapper json,HttpServletResponse response,int status,String code) throws IOException {
        response.setStatus(status);response.setContentType("application/json");response.setHeader("Cache-Control","no-store");
        json.writeValue(response.getOutputStream(),new ApiResult<>(status,code,null,System.currentTimeMillis()));
    }
    static final class DeploymentKeyFilter extends OncePerRequestFilter {
        private final DeploymentKeys keys;private final ObjectMapper json;private final DefaultBearerTokenResolver resolver=new DefaultBearerTokenResolver();
        DeploymentKeyFilter(DeploymentKeys keys,ObjectMapper json) { this.keys=keys;this.json=json; }
        @Override protected void doFilterInternal(HttpServletRequest request,HttpServletResponse response,FilterChain chain) throws ServletException,IOException {
            response.setHeader("Cache-Control","no-store");
            String token;
            try { token=resolver.resolve(request); }
            catch(org.springframework.security.oauth2.core.OAuth2AuthenticationException invalid) { failure(json,response,401,"unauthenticated");return; }
            if(token!=null&&token.startsWith(DeploymentKeys.PREFIX)) {
                try {
                    var principal=keys.authenticate(token);
                    if(principal.isEmpty()) { failure(json,response,401,"unauthenticated");return; }
                    var context=SecurityContextHolder.createEmptyContext();
                    context.setAuthentication(UsernamePasswordAuthenticationToken.authenticated(principal.get(),null,List.of(new SimpleGrantedAuthority("DEPLOYMENT_SERVICE"))));
                    SecurityContextHolder.setContext(context);
                } catch(DataAccessException unavailable) { failure(json,response,503,"execution_dependency_unavailable");return; }
            }
            chain.doFilter(request,response);
        }
    }
}
