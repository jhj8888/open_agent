package com.iflytek.astron.console.toolkit.service.openapi.impl;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

import com.iflytek.astron.console.commons.constant.ResponseEnum;
import com.iflytek.astron.console.commons.exception.BusinessException;
import com.iflytek.astron.console.commons.mapper.bot.ChatBotApiMapper;
import com.iflytek.astron.console.toolkit.entity.dto.openapi.WorkflowIoTransRequest;
import com.iflytek.astron.console.toolkit.service.external.ExternalApiService;
import com.iflytek.astron.console.toolkit.service.workflow.WorkflowService;
import java.util.Collections;
import java.util.Optional;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

@ExtendWith(MockitoExtension.class)
class OpenApiServiceImplTest {

    @Mock
    private ExternalApiService externalApiService;
    @Mock
    private WorkflowService workflowService;
    @Mock
    private ChatBotApiMapper chatBotApiMapper;

    @InjectMocks
    private OpenApiServiceImpl service;

    @Test
    void passesCompleteCredentialPairToTenantVerification() {
        WorkflowIoTransRequest request = request("api-key", "api-secret");
        when(externalApiService.verifyAppCredentials("api-key", "api-secret"))
                .thenReturn(Optional.of("app-123"));
        when(chatBotApiMapper.selectList(any())).thenReturn(Collections.emptyList());

        assertThat(service.getWorkflowIoTransformations(request)).isNull();

        verify(externalApiService).verifyAppCredentials("api-key", "api-secret");
    }

    @Test
    void rejectedCredentialPairIsUnauthorizedAndCannotReachDataLookup() {
        WorkflowIoTransRequest request = request("api-key", "wrong-secret");
        when(externalApiService.verifyAppCredentials("api-key", "wrong-secret"))
                .thenReturn(Optional.empty());

        assertThatThrownBy(() -> service.getWorkflowIoTransformations(request))
                .isInstanceOfSatisfying(BusinessException.class, exception -> assertThat(exception.getCode())
                        .isEqualTo(ResponseEnum.UNAUTHORIZED.getCode()));

        verify(externalApiService).verifyAppCredentials("api-key", "wrong-secret");
        verifyNoInteractions(chatBotApiMapper, workflowService);
    }

    private static WorkflowIoTransRequest request(String apiKey, String apiSecret) {
        WorkflowIoTransRequest request = new WorkflowIoTransRequest();
        request.setApiKey(apiKey);
        request.setApiSecret(apiSecret);
        return request;
    }
}
