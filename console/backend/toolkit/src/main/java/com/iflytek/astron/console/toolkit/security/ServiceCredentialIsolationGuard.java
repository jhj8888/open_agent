package com.iflytek.astron.console.toolkit.security;

import lombok.RequiredArgsConstructor;
import org.springframework.beans.factory.SmartInitializingSingleton;
import org.springframework.stereotype.Component;

/** Prevents two internal service capabilities from sharing a bearer credential. */
@Component
@RequiredArgsConstructor
public class ServiceCredentialIsolationGuard implements SmartInitializingSingleton {

    private final ArtifactUploadTokenProvider artifactUploadTokenProvider;
    private final SandboxRuntimeCredentialTokenProvider sandboxRuntimeCredentialTokenProvider;

    @Override
    public void afterSingletonsInstantiated() {
        if (artifactUploadTokenProvider.usesSameCredentialAs(
                sandboxRuntimeCredentialTokenProvider)) {
            throw new IllegalStateException(
                    "Workflow artifact upload and sandbox runtime credentials must be different");
        }
    }
}
