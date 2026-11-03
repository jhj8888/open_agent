package com.iflytek.astron.console.hub.service.chat.springai;

import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

import com.iflytek.astron.console.commons.service.ChatRecordModelService;
import com.iflytek.astron.console.commons.service.data.ChatDataService;
import com.iflytek.astron.console.commons.util.SseEmitterUtil;
import com.iflytek.astron.console.hub.service.agentmemory.runtime.AgentMemoryRuntimeService;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import org.junit.jupiter.api.Test;
import org.springframework.ai.chat.model.ChatResponse;
import org.springframework.ai.chat.prompt.Prompt;
import org.springframework.ai.openai.OpenAiChatModel;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;
import reactor.core.publisher.Flux;

class AgentStreamCancellationTest {
    @Test void stopCancelsSilentUpstreamWithoutWaitingForAnotherToken() throws Exception {
        CountDownLatch cancelled=new CountDownLatch(1);
        var model=mock(OpenAiChatModel.class);
        when(model.stream(any(Prompt.class))).thenReturn(Flux.<ChatResponse>never().doOnCancel(cancelled::countDown));
        var factory=mock(ChatModelFactory.class);
        when(factory.forSparkModel(anyString())).thenReturn(new ChatModelFactory.AgentModel(model,"synthetic"));
        var tools=mock(AgentToolCallbackResolver.class);
        when(tools.resolve(any(),any(),any(),any(),any(),any())).thenReturn(List.of());
        var memory=mock(AgentMemoryRuntimeService.class);
        var service=new SpringAiAgentChatService(factory,tools,mock(ChatRecordModelService.class),mock(ChatDataService.class),memory,Runnable::run);
        var task=AgentChatTask.builder().sparkModelName("synthetic").messages(List.of()).userId("test-user").debug(true).build();
        String id="cancel-test-"+UUID.randomUUID();
        service.chat(task,new SseEmitter(),id);
        SseEmitterUtil.stopStream(id);
        assertTrue(cancelled.await(2,TimeUnit.SECONDS));
        verifyNoInteractions(memory);
    }
}
