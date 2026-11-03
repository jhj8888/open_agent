package com.iflytek.astron.console.commons.workflow;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import okhttp3.MediaType;
import okhttp3.RequestBody;
import org.junit.jupiter.api.Test;
import org.springframework.test.util.ReflectionTestUtils;

class WorkflowClientInternalAuthTest {

    private static final RequestBody EMPTY_JSON = RequestBody.create(
            "{}", MediaType.parse("application/json"));

    @Test
    void constructorNormalizesInternalKeyBeforeTheAsynchronousRequest() {
        String internalKey = "0123456789abcdef0123456789abcdef";

        WorkflowClient client = new WorkflowClient(
                "http://127.0.0.1/workflow/v1/chat/completions",
                "app-id",
                "app-key",
                "app-secret",
                EMPTY_JSON,
                "  " + internalKey + "  ");

        assertThat(ReflectionTestUtils.getField(client, "workflowInternalApiKey"))
                .isEqualTo(internalKey);
    }

    @Test
    void constructorRejectsPublishedPlaceholderBeforeTheAsynchronousRequest() {
        assertThatThrownBy(() -> new WorkflowClient(
                "http://127.0.0.1/workflow/v1/chat/completions",
                "app-id",
                "app-key",
                "app-secret",
                EMPTY_JSON,
                "CHANGE_ME_WORKFLOW_INTERNAL_API_KEY"))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageNotContaining("CHANGE_ME_WORKFLOW_INTERNAL_API_KEY");
    }
}
