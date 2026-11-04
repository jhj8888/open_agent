package com.iflytek.astron.console.hub.platform;

import com.iflytek.astron.console.commons.response.ApiResult;
import com.iflytek.astron.console.commons.tenant.TenantAccessDeniedException;
import org.springframework.core.Ordered;
import org.springframework.core.annotation.Order;
import org.springframework.dao.OptimisticLockingFailureException;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;

/** Scoped advice preserves existing upstream error contracts. Never echo inaccessible IDs. */
@Order(Ordered.HIGHEST_PRECEDENCE)
@RestControllerAdvice(basePackages = {"com.iflytek.astron.console.hub.platform", "com.iflytek.astron.platform"})
public class PlatformExceptionHandler {
    @ExceptionHandler(CurrentProfileService.ProfileUnavailable.class)
    public ResponseEntity<ApiResult<Void>> profileUnavailable() {
        return error(HttpStatus.FORBIDDEN, "profile_unavailable");
    }
    @ExceptionHandler(com.iflytek.astron.console.hub.platform.run.RunFailure.class)
    public ResponseEntity<ApiResult<Void>> runFailure(com.iflytek.astron.console.hub.platform.run.RunFailure exception) {
        HttpStatus status = switch (exception.code()) {
            case "unauthenticated" -> HttpStatus.UNAUTHORIZED;
            case "action_forbidden" -> HttpStatus.FORBIDDEN;
            case "executor_unavailable", "stored_event_unavailable" -> HttpStatus.SERVICE_UNAVAILABLE;
            case "resource_unavailable" -> HttpStatus.NOT_FOUND;
            case "queue_full", "sandbox_quota_full", "stream_limit", "credential_limit" -> HttpStatus.TOO_MANY_REQUESTS;
            case "successful_test_required", "skill_verification_required", "production_promotion_required", "credential_revoked", "credential_expired", "skill_unavailable", "model_unavailable" -> HttpStatus.CONFLICT;
            case "sandbox_state_conflict", "sandbox_cleanup_pending", "cleanup_before_terminal" -> HttpStatus.CONFLICT;
            case "supervision_conflict", "cleanup_proof_conflict", "cleanup_unverified" -> HttpStatus.CONFLICT;
            case "idempotency_conflict", "event_conflict", "state_conflict", "lease_fenced", "deployment_unavailable", "tenant_inactive" -> HttpStatus.CONFLICT;
            case "event_history_expired" -> HttpStatus.GONE;
            default -> HttpStatus.UNPROCESSABLE_ENTITY;
        };
        return error(status, exception.code());
    }
    @ExceptionHandler(com.iflytek.astron.console.hub.platform.skill.SkillReleaseService.ReleaseConflictException.class)
    public ResponseEntity<ApiResult<Void>> skillConflict(RuntimeException exception) {
        return error(HttpStatus.CONFLICT, exception.getMessage());
    }

    @ExceptionHandler(com.iflytek.astron.console.hub.platform.skill.MinioSkillArtifactStore.ArtifactUnavailableException.class)
    public ResponseEntity<ApiResult<Void>> artifactUnavailable() {
        return error(HttpStatus.SERVICE_UNAVAILABLE, "Skill artifact storage is unavailable or content verification failed");
    }

    @ExceptionHandler(TenantAccessDeniedException.class)
    public ResponseEntity<ApiResult<Void>> denied() {
        return error(HttpStatus.NOT_FOUND, "Resource unavailable");
    }

    @ExceptionHandler(OptimisticLockingFailureException.class)
    public ResponseEntity<ApiResult<Void>> conflict() {
        return error(HttpStatus.CONFLICT, "Configuration changed; refresh before retrying");
    }

    @ExceptionHandler(org.springframework.dao.DataAccessException.class)
    public ResponseEntity<ApiResult<Void>> storageUnavailable() {
        // SQL statements and driver messages may contain private request data.
        return error(HttpStatus.SERVICE_UNAVAILABLE, "execution_dependency_unavailable");
    }

    @ExceptionHandler(IllegalArgumentException.class)
    public ResponseEntity<ApiResult<Void>> invalid(IllegalArgumentException exception) {
        return error(HttpStatus.BAD_REQUEST, exception.getMessage());
    }

    // Default Spring validation logging includes rejected field values, which can contain keys.
    @ExceptionHandler({org.springframework.web.bind.MethodArgumentNotValidException.class,
            org.springframework.http.converter.HttpMessageNotReadableException.class})
    public ResponseEntity<ApiResult<Void>> invalidBody(jakarta.servlet.http.HttpServletRequest request) {
        String path=request.getRequestURI().substring(request.getContextPath().length());
        return path.startsWith("/api/v1/")
                ? error(HttpStatus.UNPROCESSABLE_ENTITY,"invalid_input")
                : error(HttpStatus.BAD_REQUEST, "Invalid request fields");
    }

    private ResponseEntity<ApiResult<Void>> error(HttpStatus status, String message) {
        // Preflight failures on an SSE route still have a JSON error body. An
        // explicit media type prevents Accept: text/event-stream from turning
        // the intended 4xx response into an unhandled negotiation exception.
        return ResponseEntity.status(status).contentType(org.springframework.http.MediaType.APPLICATION_JSON)
                .body(new ApiResult<>(status.value(), message, null, System.currentTimeMillis()));
    }
}
