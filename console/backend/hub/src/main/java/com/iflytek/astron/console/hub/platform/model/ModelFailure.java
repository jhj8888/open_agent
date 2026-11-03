package com.iflytek.astron.console.hub.platform.model;

/** Safe, stable codes shared with the Python adapter. Never copy a provider body. */
public record ModelFailure(String code, String message) {
    public static ModelFailure from(Throwable error) {
        Throwable current=error;
        for(int depth=0; current!=null && depth<8; depth++, current=current.getCause()) {
            if(current instanceof ModelEndpointPolicy.EndpointDeniedException) return new ModelFailure("MODEL_ENDPOINT_DENIED","模型地址未获平台批准");
            if(current instanceof java.util.concurrent.TimeoutException || current instanceof java.net.SocketTimeoutException
                    || current instanceof io.netty.handler.timeout.TimeoutException) return new ModelFailure("MODEL_TIMEOUT","模型调用超时");
            int status=0;
            if(current instanceof ModelHttpClients.ModelUpstreamException upstream) status=upstream.status();
            if(current instanceof org.springframework.web.reactive.function.client.WebClientResponseException response) status=response.getStatusCode().value();
            if(status==401 || status==403) return new ModelFailure("MODEL_AUTH_FAILED","模型接口认证失败");
            if(status==429) return new ModelFailure("MODEL_RATE_LIMITED","模型接口限流");
            if(status>=300 && status<400) return new ModelFailure("MODEL_ENDPOINT_DENIED","模型接口重定向已拒绝");
            if(status>=400) return new ModelFailure("MODEL_UPSTREAM_ERROR","模型接口返回错误");
            if(current instanceof java.net.ConnectException || current instanceof reactor.netty.http.client.PrematureCloseException)
                return new ModelFailure("MODEL_CONNECTION_FAILED","模型连接失败或响应流中断");
        }
        return new ModelFailure("MODEL_PROTOCOL_ERROR","模型响应不符合接口契约");
    }
}
