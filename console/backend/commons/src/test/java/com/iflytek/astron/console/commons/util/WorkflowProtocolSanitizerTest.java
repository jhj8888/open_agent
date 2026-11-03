package com.iflytek.astron.console.commons.util;

import static org.assertj.core.api.Assertions.assertThat;

import com.alibaba.fastjson2.JSON;
import com.alibaba.fastjson2.JSONArray;
import com.alibaba.fastjson2.JSONObject;
import com.iflytek.astron.console.commons.entity.workflow.Workflow;
import java.util.List;
import org.junit.jupiter.api.Test;

class WorkflowProtocolSanitizerTest {

    @Test
    void removesDeepSandboxAndLegacyFieldsButKeepsModelApiKey() {
        String protocol = """
                {"nodes":[{"data":{"nodeParam":{"apiKey":"model-key","children":[
                  {"sandbox":{"apiKey":"sandbox-key","uid":"user-1"}},
                  {"artifact_upload_token":"artifact-secret"},
                  {"runtimeConfigUrl":"http://internal/runtime"}
                ]}}}],"api_key":"another-model-key"}
                """;

        String sanitized = WorkflowProtocolSanitizer.sanitize(protocol);
        JSONObject root = JSON.parseObject(sanitized);
        JSONArray children = root.getJSONArray("nodes")
                .getJSONObject(0)
                .getJSONObject("data")
                .getJSONObject("nodeParam")
                .getJSONArray("children");

        assertThat(sanitized)
                .doesNotContain("sandbox-key", "artifact-secret", "internal/runtime");
        assertThat(children.getJSONObject(0)).doesNotContainKey("sandbox");
        assertThat(root.getJSONArray("nodes")
                .getJSONObject(0)
                .getJSONObject("data")
                .getJSONObject("nodeParam")
                .getString("apiKey"))
                .isEqualTo("model-key");
        assertThat(root.getString("api_key")).isEqualTo("another-model-key");
    }

    @Test
    void systemProtocolKeepsOnlyNonSecretSandboxExecutionReferences() {
        String protocol = """
                {"node":{"apiKey":"model-key","sandbox":{
                  "enabled":true,"uid":"user-1","space_id":"7","workflowId":"flow-1",
                  "run_id":"run-1","nodeId":"node-1","provider":"e2b","apiKey":"secret",
                  "timeoutSeconds":60,"allowInternetAccess":true,
                  "artifactUploadUrl":"http://internal/upload",
                  "runtimeCredentialToken":"runtime-secret"
                }}}
                """;

        String sanitized = WorkflowProtocolSanitizer.sanitizeSystemProtocol(protocol);
        JSONObject node = JSON.parseObject(sanitized).getJSONObject("node");
        JSONObject sandbox = node.getJSONObject("sandbox");

        assertThat(node.getString("apiKey")).isEqualTo("model-key");
        assertThat(sandbox)
                .containsEntry("enabled", true)
                .containsEntry("uid", "user-1")
                .containsEntry("space_id", "7")
                .containsEntry("workflowId", "flow-1")
                .containsEntry("run_id", "run-1")
                .containsEntry("nodeId", "node-1")
                .doesNotContainKeys(
                        "provider",
                        "apiKey",
                        "timeoutSeconds",
                        "allowInternetAccess",
                        "artifactUploadUrl",
                        "runtimeCredentialToken");
        assertThat(sanitized).doesNotContain("secret", "internal/upload");
    }

    @Test
    void reportsInvalidJsonAndFailsClosedForEntityResponses() {
        String invalid = "{\"sandbox\":{\"apiKey\":\"must-not-leak\"}";

        WorkflowProtocolSanitizer.SanitizationResult result =
                WorkflowProtocolSanitizer.analyze(invalid);
        Workflow workflow = new Workflow();
        workflow.setData(invalid);
        workflow.setPublishedData(invalid);

        assertThat(result.validJson()).isFalse();
        assertThat(result.sanitizedJson()).isNull();
        assertThat(workflow.getData()).isNull();
        assertThat(workflow.getPublishedData()).isNull();
    }

    @Test
    void rejectsNonObjectJsonRootsIncludingDoubleEncodedSecrets() {
        String doubleEncodedSecret = JSON.toJSONString(
                "{\"sandbox\":{\"apiKey\":\"must-not-leak\"}}");

        for (String invalidProtocol : List.of(
                doubleEncodedSecret,
                "123",
                "true",
                "null",
                "[{\"sandbox\":{\"apiKey\":\"must-not-leak\"}}]")) {
            WorkflowProtocolSanitizer.SanitizationResult result =
                    WorkflowProtocolSanitizer.analyze(invalidProtocol);

            assertThat(result.validJson()).isFalse();
            assertThat(result.sanitizedJson()).isNull();
            assertThat(WorkflowProtocolSanitizer.sanitizeSystemProtocol(invalidProtocol)).isNull();
        }
    }

    @Test
    void leavesValidProtocolByteForByteUnchangedWhenNoSensitiveFieldExists() {
        String protocol = " { \"node\" : { \"apiKey\" : \"model-key\" } } ";

        WorkflowProtocolSanitizer.SanitizationResult result =
                WorkflowProtocolSanitizer.analyze(protocol);

        assertThat(result.validJson()).isTrue();
        assertThat(result.changed()).isFalse();
        assertThat(result.sanitizedJson()).isSameAs(protocol);
    }

    @Test
    void sanitizesEmbeddedAndDoubleEmbeddedProtocolObjects() {
        String nestedPlugin = JSON.toJSONString(new JSONObject()
                .fluentPut("name", "plugin")
                .fluentPut("runtimeCredentialToken", "nested-runtime-secret")
                .fluentPut(
                        "config",
                        JSON.toJSONString(new JSONObject()
                                .fluentPut("sandbox", new JSONObject()
                                        .fluentPut("apiKey", "double-embedded-secret"))
                                .fluentPut("label", "keep-me"))));
        String protocol = new JSONObject()
                .fluentPut("nodeParam", new JSONObject().fluentPut("plugin", nestedPlugin))
                .toJSONString();

        String sanitized = WorkflowProtocolSanitizer.sanitize(protocol);
        String sanitizedPlugin = JSON.parseObject(sanitized)
                .getJSONObject("nodeParam")
                .getString("plugin");
        JSONObject plugin = JSON.parseObject(sanitizedPlugin);
        JSONObject config = JSON.parseObject(plugin.getString("config"));

        assertThat(plugin).doesNotContainKey("runtimeCredentialToken");
        assertThat(config).doesNotContainKey("sandbox").containsEntry("label", "keep-me");
        assertThat(sanitized)
                .doesNotContain("nested-runtime-secret", "double-embedded-secret");
    }

    @Test
    void leavesInvalidScalarAndSafeEmbeddedStringsByteForByteUnchanged() {
        String invalid = "{not-json";
        String scalar = " true ";
        String safeObject = " { \"label\" : \"keep formatting\" } ";
        String protocol = new JSONObject()
                .fluentPut("invalid", invalid)
                .fluentPut("scalar", scalar)
                .fluentPut("safeObject", safeObject)
                .toJSONString();

        WorkflowProtocolSanitizer.SanitizationResult result =
                WorkflowProtocolSanitizer.analyze(protocol);

        assertThat(result.changed()).isFalse();
        assertThat(result.sanitizedJson()).isSameAs(protocol);
        JSONObject unchanged = JSON.parseObject(result.sanitizedJson());
        assertThat(unchanged.getString("invalid")).isEqualTo(invalid);
        assertThat(unchanged.getString("scalar")).isEqualTo(scalar);
        assertThat(unchanged.getString("safeObject")).isEqualTo(safeObject);
    }

    @Test
    void failsClosedWhenEmbeddedJsonNestingExceedsSafetyLimit() {
        String embedded = new JSONObject()
                .fluentPut("sandbox", new JSONObject().fluentPut("apiKey", "secret"))
                .toJSONString();
        for (int depth = 0; depth < 10; depth++) {
            embedded = new JSONObject().fluentPut("next", embedded).toJSONString();
        }
        String protocol = new JSONObject().fluentPut("plugin", embedded).toJSONString();

        WorkflowProtocolSanitizer.SanitizationResult result =
                WorkflowProtocolSanitizer.analyze(protocol);

        assertThat(result.validJson()).isFalse();
        assertThat(result.sanitizedJson()).isNull();
    }
}
