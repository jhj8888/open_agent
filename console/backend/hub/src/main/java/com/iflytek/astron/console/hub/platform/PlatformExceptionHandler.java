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
@RestControllerAdvice(basePackages = "com.iflytek.astron.console.hub.platform")
public class PlatformExceptionHandler {
    @ExceptionHandler(TenantAccessDeniedException.class)
    public ResponseEntity<ApiResult<Void>> denied() {
        return error(HttpStatus.NOT_FOUND, "Resource unavailable");
    }

    @ExceptionHandler(OptimisticLockingFailureException.class)
    public ResponseEntity<ApiResult<Void>> conflict() {
        return error(HttpStatus.CONFLICT, "Configuration changed; refresh before retrying");
    }

    @ExceptionHandler(IllegalArgumentException.class)
    public ResponseEntity<ApiResult<Void>> invalid(IllegalArgumentException exception) {
        return error(HttpStatus.BAD_REQUEST, exception.getMessage());
    }

    private ResponseEntity<ApiResult<Void>> error(HttpStatus status, String message) {
        return ResponseEntity.status(status).body(new ApiResult<>(status.value(), message, null, System.currentTimeMillis()));
    }
}
