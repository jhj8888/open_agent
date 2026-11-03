package com.iflytek.astron.console.hub.config;

import static org.assertj.core.api.Assertions.assertThat;

import com.iflytek.astron.console.toolkit.config.properties.SandboxRuntimeCredentialProperties;
import com.iflytek.astron.console.toolkit.config.properties.SkillSandboxArtifactProperties;
import java.nio.file.Path;
import java.util.Map;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.boot.context.properties.bind.validation.BindValidationException;
import org.springframework.boot.test.context.ConfigDataApplicationContextInitializer;
import org.springframework.boot.test.context.runner.ApplicationContextRunner;
import org.springframework.context.annotation.Configuration;
import org.springframework.core.env.ConfigurableEnvironment;
import org.springframework.core.env.StandardEnvironment;
import org.springframework.core.env.SystemEnvironmentPropertySource;

class SkillSandboxDeploymentConfigurationTest {

    private static final String ARTIFACT_TOKEN_FILE_ENV =
            "SKILL_SANDBOX_ARTIFACT_UPLOAD_TOKEN_FILE";
    private static final String RUNTIME_TOKEN_FILE_ENV =
            "SKILL_SANDBOX_RUNTIME_CREDENTIAL_TOKEN_FILE";
    private static final String ARTIFACT_TOKEN_FILE_PROPERTY =
            "skill.sandbox.artifact-upload-token-file";
    private static final String RUNTIME_TOKEN_FILE_PROPERTY =
            "skill.sandbox.runtime-credential.token-file";

    private final ApplicationContextRunner contextRunner = new ApplicationContextRunner()
            .withInitializer(new ConfigDataApplicationContextInitializer())
            .withUserConfiguration(SandboxDeploymentPropertiesConfiguration.class);

    @TempDir
    Path temporaryDirectory;

    @Test
    void bindsIndependentTokenFilePathsFromApplicationYamlEnvironmentPlaceholders() {
        Path artifactTokenFile = temporaryDirectory.resolve("artifact-upload-token.not-a-secret");
        Path runtimeTokenFile = temporaryDirectory.resolve("runtime-credential-token.not-a-secret");

        withEnvironment(Map.of(
                ARTIFACT_TOKEN_FILE_ENV,
                artifactTokenFile.toString(),
                RUNTIME_TOKEN_FILE_ENV,
                runtimeTokenFile.toString()))
                .run(context -> {
                    assertThat(context).hasNotFailed();
                    assertApplicationYamlDefinesPlaceholder(
                            context.getEnvironment(),
                            ARTIFACT_TOKEN_FILE_PROPERTY,
                            "${" + ARTIFACT_TOKEN_FILE_ENV + ":}");
                    assertApplicationYamlDefinesPlaceholder(
                            context.getEnvironment(),
                            RUNTIME_TOKEN_FILE_PROPERTY,
                            "${" + RUNTIME_TOKEN_FILE_ENV + ":}");
                    assertThat(context.getEnvironment().getProperty(ARTIFACT_TOKEN_FILE_PROPERTY))
                            .isEqualTo(artifactTokenFile.toString());
                    assertThat(context.getEnvironment().getProperty(RUNTIME_TOKEN_FILE_PROPERTY))
                            .isEqualTo(runtimeTokenFile.toString());

                    SkillSandboxArtifactProperties artifactProperties =
                            context.getBean(SkillSandboxArtifactProperties.class);
                    SandboxRuntimeCredentialProperties runtimeProperties =
                            context.getBean(SandboxRuntimeCredentialProperties.class);

                    assertThat(artifactProperties.getArtifactUploadTokenFile())
                            .isEqualTo(artifactTokenFile.toString());
                    assertThat(runtimeProperties.getTokenFile())
                            .isEqualTo(runtimeTokenFile.toString())
                            .isNotEqualTo(artifactProperties.getArtifactUploadTokenFile());
                });
    }

    @Test
    void failsValidationWhenArtifactTokenSourceIsMissing() {
        Path runtimeTokenFile = temporaryDirectory.resolve("runtime-only-token.not-a-secret");

        withEnvironment(Map.of(RUNTIME_TOKEN_FILE_ENV, runtimeTokenFile.toString()))
                .run(context -> {
                    assertThat(context).hasFailed();
                    assertThat(context.getStartupFailure())
                            .hasRootCauseInstanceOf(BindValidationException.class)
                            .hasStackTraceContaining(
                                    "skill.sandbox artifact upload token or token file must be configured");
                });
    }

    @Test
    void failsValidationWhenRuntimeCredentialSourceIsMissing() {
        Path artifactTokenFile = temporaryDirectory.resolve("artifact-only-token.not-a-secret");

        withEnvironment(Map.of(ARTIFACT_TOKEN_FILE_ENV, artifactTokenFile.toString()))
                .run(context -> {
                    assertThat(context).hasFailed();
                    assertThat(context.getStartupFailure())
                            .hasRootCauseInstanceOf(BindValidationException.class)
                            .hasStackTraceContaining(
                                    "skill.sandbox runtime credential token or token file must be configured");
                });
    }

    private ApplicationContextRunner withEnvironment(Map<String, Object> environmentVariables) {
        return contextRunner.withInitializer(context -> {
            var propertySources = context.getEnvironment().getPropertySources();
            propertySources.remove(StandardEnvironment.SYSTEM_ENVIRONMENT_PROPERTY_SOURCE_NAME);
            propertySources.addLast(new SystemEnvironmentPropertySource(
                    StandardEnvironment.SYSTEM_ENVIRONMENT_PROPERTY_SOURCE_NAME,
                    environmentVariables));
        });
    }

    private void assertApplicationYamlDefinesPlaceholder(
            ConfigurableEnvironment environment, String propertyName, String placeholder) {
        assertThat(environment.getPropertySources())
                .filteredOn(propertySource -> propertySource.getName().contains("application.yml"))
                .anySatisfy(propertySource -> assertThat(propertySource.getProperty(propertyName)).hasToString(placeholder));
    }

    @Configuration(proxyBeanMethods = false)
    @EnableConfigurationProperties({
            SkillSandboxArtifactProperties.class,
            SandboxRuntimeCredentialProperties.class
    })
    static class SandboxDeploymentPropertiesConfiguration {
    }
}
