package com.iflytek.astron.platform;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.iflytek.astron.console.commons.response.ApiResult;
import com.iflytek.astron.console.commons.tenant.TenantAccessDeniedException;
import com.iflytek.astron.console.hub.platform.TenantGovernanceService;
import jakarta.annotation.PreDestroy;
import java.net.URI;
import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.time.Instant;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.springframework.core.env.Environment;
import org.springframework.http.client.JdkClientHttpRequestFactory;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.client.RestClient;

/** Fixed operations queries only. There is no user-controlled PromQL or upstream URL. */
@RestController
public class HostMetricsController {
    private static final String NODE="{job=\"open-agent-node\"}";
    private static final Map<String,String> EXPRESSIONS=Map.ofEntries(
        Map.entry("cpuPercent","100 * (1 - avg(rate(node_cpu_seconds_total{job=\"open-agent-node\",mode=\"idle\"}[2m])))"),
        Map.entry("memoryPercent","100 * (1 - node_memory_MemAvailable_bytes"+NODE+" / node_memory_MemTotal_bytes"+NODE+")"),
        Map.entry("memoryTotalBytes","node_memory_MemTotal_bytes"+NODE),
        Map.entry("diskPercent","100 * (1 - node_filesystem_avail_bytes{job=\"open-agent-node\",mountpoint=\"/\"} / node_filesystem_size_bytes{job=\"open-agent-node\",mountpoint=\"/\"})"),
        Map.entry("load1","node_load1"+NODE),
        Map.entry("sourceTimestamp","timestamp(node_time_seconds"+NODE+")"),
        Map.entry("nodeUp","up{job=\"open-agent-node\"}"),
        Map.entry("apiUp","up{job=\"open-agent-platform\"}"),
        Map.entry("metricsHealthy","open_agent_metrics_collection_success{job=\"open-agent-platform\"}"),
        Map.entry("supervisorUp","node_systemd_unit_state{job=\"open-agent-node\",name=\"open-agent-supervisor.service\",state=\"active\"}"),
        Map.entry("temporalUp","node_systemd_unit_state{job=\"open-agent-node\",name=\"open-agent-temporal.service\",state=\"active\"}"),
        Map.entry("ingressUp","node_systemd_unit_state{job=\"open-agent-node\",name=\"open-agent-ingress.service\",state=\"active\"}"));
    static final String QUERY=EXPRESSIONS.entrySet().stream()
        .map(e->"label_replace(("+e.getValue()+"),\"metric\",\""+e.getKey()+"\",\"\",\"\")")
        .collect(java.util.stream.Collectors.joining(" or "));
    private final TenantGovernanceService governance;
    private final ObjectMapper json;
    private final HttpClient transport;
    private final RestClient client;
    private final URI query;

    public HostMetricsController(TenantGovernanceService governance,ObjectMapper json,Environment environment) {
        this.governance=governance;this.json=json;
        transport=HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(2)).followRedirects(HttpClient.Redirect.NEVER).build();
        var factory=new JdkClientHttpRequestFactory(transport);factory.setReadTimeout(Duration.ofSeconds(3));
        client=RestClient.builder().requestFactory(factory).build();
        String base=environment.getProperty("PLATFORM_PROMETHEUS_URL","");
        if (base.isBlank()) { query=null; return; }
        URI uri=URI.create(base);
        if (!List.of("http","https").contains(uri.getScheme()) || uri.getHost()==null || uri.getUserInfo()!=null
                || uri.getRawQuery()!=null || uri.getFragment()!=null || !(uri.getPath().isEmpty()||uri.getPath().equals("/")))
            throw new IllegalStateException("Invalid operations metrics endpoint configuration");
        query=URI.create(base.replaceAll("/$","")+"/api/v1/query?timeout=2s&limit=32&query="+URLEncoder.encode(QUERY,StandardCharsets.UTF_8));
    }

    @GetMapping("/platform/operations/host-metrics")
    public ApiResult<?> snapshot() {
        if (!governance.isPlatformAdmin()) throw new TenantAccessDeniedException();
        if (query==null) return ApiResult.success(Map.of("available",false,"reason","not_configured"));
        try {
            byte[] body=client.get().uri(query).exchange((request,response)->{
                if (!response.getStatusCode().is2xxSuccessful()) throw new IllegalStateException();
                byte[] bytes=response.getBody().readNBytes(65537);
                if(bytes.length>65536) throw new IllegalStateException();
                return bytes;
            });
            var root=json.readTree(body);
            if (!root.path("status").asText().equals("success") || !root.at("/data/resultType").asText().equals("vector"))
                throw new IllegalStateException();
            var values=new LinkedHashMap<String,Double>();
            for (var row:root.at("/data/result")) {
                String name=row.at("/metric/metric").asText();
                if (!EXPRESSIONS.containsKey(name) || values.containsKey(name)) throw new IllegalStateException();
                double value=Double.parseDouble(row.path("value").get(1).asText());
                if (Double.isFinite(value)) values.put(name,value);
            }
            double stamp=values.getOrDefault("sourceTimestamp",0.0);
            boolean fresh=stamp>Instant.now().getEpochSecond()-60 && stamp<=Instant.now().getEpochSecond()+5;
            if (!fresh || values.getOrDefault("nodeUp",0.0)!=1.0)
                return ApiResult.success(Map.of("available",false,"reason","source_stale"));
            return ApiResult.success(Map.of("available",true,"sampledAt",Instant.ofEpochSecond((long)stamp),"values",values));
        } catch (Exception unavailable) {
            return ApiResult.success(Map.of("available",false,"reason","source_unavailable"));
        }
    }
    @PreDestroy public void close() { transport.shutdownNow(); }
}
