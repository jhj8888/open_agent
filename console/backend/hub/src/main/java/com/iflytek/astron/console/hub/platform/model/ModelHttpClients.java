package com.iflytek.astron.console.hub.platform.model;

import io.netty.channel.ChannelOption;
import io.netty.handler.ssl.SslContextBuilder;
import io.netty.resolver.AbstractAddressResolver;
import io.netty.resolver.AddressResolver;
import io.netty.resolver.AddressResolverGroup;
import io.netty.util.concurrent.EventExecutor;
import io.netty.util.concurrent.Promise;
import java.io.File;
import java.net.InetSocketAddress;
import java.time.Duration;
import java.util.List;
import org.springframework.http.client.ReactorClientHttpRequestFactory;
import org.springframework.http.client.reactive.ReactorClientHttpConnector;
import org.springframework.web.client.RestClient;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.netty.http.client.HttpClient;

/** Both Spring AI transports use the same pinned DNS answers and timeout policy. */
public record ModelHttpClients(RestClient.Builder rest, WebClient.Builder web) {
    public static ModelHttpClients create(ModelEndpointPolicy.Approved approved, int connectSeconds, int readSeconds, String caFile) {
        if (connectSeconds < 1 || connectSeconds > 60 || readSeconds < 1 || readSeconds > 1800) {
            throw new IllegalArgumentException("Invalid model timeout");
        }
        var uri = approved.uri();
        AddressResolverGroup<InetSocketAddress> pinned = new AddressResolverGroup<>() {
            @Override
            protected AddressResolver<InetSocketAddress> newResolver(EventExecutor executor) {
                return new AbstractAddressResolver<>(executor, InetSocketAddress.class) {
                    @Override protected boolean doIsResolved(InetSocketAddress address) { return false; }
                    private List<InetSocketAddress> addresses(InetSocketAddress request) {
                        if (!request.getHostString().equalsIgnoreCase(uri.getHost()) || request.getPort() != ModelEndpointPolicy.port(uri)) {
                            throw ModelEndpointPolicy.denied();
                        }
                        return approved.addresses().stream().map(ip -> new InetSocketAddress(ip, request.getPort())).toList();
                    }
                    @Override protected void doResolve(InetSocketAddress address, Promise<InetSocketAddress> promise) {
                        try { promise.setSuccess(addresses(address).getFirst()); } catch (RuntimeException error) { promise.setFailure(error); }
                    }
                    @Override protected void doResolveAll(InetSocketAddress address, Promise<List<InetSocketAddress>> promise) {
                        try { promise.setSuccess(addresses(address)); } catch (RuntimeException error) { promise.setFailure(error); }
                    }
                };
            }
        };
        HttpClient http = HttpClient.newConnection().resolver(pinned).followRedirect(false).disableRetry(true)
                .option(ChannelOption.CONNECT_TIMEOUT_MILLIS, connectSeconds * 1000)
                .responseTimeout(Duration.ofSeconds(readSeconds))
                .doOnResponse((response, connection) -> {
                    if (response.status().code() >= 300) throw new ModelUpstreamException(response.status().code());
                });
        if (caFile != null && !caFile.isBlank()) {
            try {
                var context = SslContextBuilder.forClient().trustManager(new File(caFile)).build();
                http = http.secure(spec -> spec.sslContext(context));
            } catch (Exception exception) {
                throw new IllegalArgumentException("Model CA configuration is unavailable");
            }
        }
        var requestFactory = new ReactorClientHttpRequestFactory(http);
        requestFactory.setReadTimeout(Duration.ofSeconds(readSeconds));
        return new ModelHttpClients(RestClient.builder().requestFactory(requestFactory),
                WebClient.builder().clientConnector(new ReactorClientHttpConnector(http)));
    }

    public static final class ModelUpstreamException extends RuntimeException {
        private final int status;
        public ModelUpstreamException(int status) { super("Model upstream HTTP " + status); this.status = status; }
        public int status() { return status; }
    }
}
