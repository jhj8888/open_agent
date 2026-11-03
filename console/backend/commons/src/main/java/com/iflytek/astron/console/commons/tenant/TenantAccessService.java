package com.iflytek.astron.console.commons.tenant;

import com.iflytek.astron.console.commons.util.RequestContextUtil;
import org.springframework.stereotype.Service;

/** Entry point shared by resource services. Call again for each request, including downloads. */
@Service
public class TenantAccessService {
    private final TenantAccessRepository repository;

    public TenantAccessService(TenantAccessRepository repository) {
        this.repository = repository;
    }

    public TenantContext require(String tenantId, String environment, TenantPermission permission) {
        String uid = RequestContextUtil.getUID();
        TenantEnvironment scope;
        try {
            scope = TenantEnvironment.parse(environment);
        } catch (IllegalArgumentException exception) {
            throw new TenantAccessDeniedException();
        }
        if (tenantId == null || tenantId.isBlank() || tenantId.length() > 80) {
            throw new TenantAccessDeniedException();
        }
        TenantContext context = repository.findContext(uid, tenantId, scope)
                .orElseThrow(TenantAccessDeniedException::new);
        context.require(permission);
        return context;
    }
}
