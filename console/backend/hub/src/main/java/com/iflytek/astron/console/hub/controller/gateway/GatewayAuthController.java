package com.iflytek.astron.console.hub.controller.gateway;

import com.iflytek.astron.console.commons.security.WorkflowGatewayIdentity;
import com.iflytek.astron.console.hub.service.gateway.GatewayAuthException;
import com.iflytek.astron.console.hub.service.gateway.GatewayAuthService;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.CacheControl;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/internal/gateway/auth")
public class GatewayAuthController {

    private static final String CONSUMER_USERNAME_HEADER = "X-Consumer-Username";
    private static final String ORIGINAL_URI_HEADER = "X-Original-URI";
    private static final String ORIGINAL_METHOD_HEADER = "X-Original-Method";

    private final GatewayAuthService gatewayAuthService;
    private final String workflowInternalApiKey;

    public GatewayAuthController(
            GatewayAuthService gatewayAuthService,
            @Value("${workflow.internal-api-key:}") String workflowInternalApiKey) {
        this.gatewayAuthService = gatewayAuthService;
        this.workflowInternalApiKey = workflowInternalApiKey;
    }

    @GetMapping("/workflow")
    public ResponseEntity<Void> authWorkflow(
            @RequestHeader(value = HttpHeaders.AUTHORIZATION, required = false) String authorizationHeader,
            @RequestHeader(value = ORIGINAL_URI_HEADER, required = false) String originalUri,
            @RequestHeader(value = ORIGINAL_METHOD_HEADER, required = false) String originalMethod) {
        try {
            String authorizedPath =
                    WorkflowGatewayIdentity.requireAuthorizedPath(originalMethod, originalUri);
            String appId = gatewayAuthService.authenticateWorkflow(authorizationHeader);
            long timestamp = System.currentTimeMillis() / 1000L;
            String signature = WorkflowGatewayIdentity.sign(
                    workflowInternalApiKey,
                    originalMethod,
                    authorizedPath,
                    appId,
                    timestamp);
            return ResponseEntity.noContent()
                    .cacheControl(CacheControl.noStore())
                    .header(CONSUMER_USERNAME_HEADER, appId)
                    .header(
                            WorkflowGatewayIdentity.TIMESTAMP_HEADER,
                            Long.toString(timestamp))
                    .header(WorkflowGatewayIdentity.SIGNATURE_HEADER, signature)
                    .build();
        } catch (GatewayAuthException | IllegalArgumentException ex) {
            return ResponseEntity.status(HttpStatus.UNAUTHORIZED)
                    .cacheControl(CacheControl.noStore())
                    .build();
        } catch (IllegalStateException ex) {
            return ResponseEntity.status(HttpStatus.SERVICE_UNAVAILABLE)
                    .cacheControl(CacheControl.noStore())
                    .build();
        }
    }
}
