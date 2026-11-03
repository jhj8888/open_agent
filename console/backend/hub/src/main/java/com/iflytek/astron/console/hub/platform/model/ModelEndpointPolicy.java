package com.iflytek.astron.console.hub.platform.model;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import io.netty.util.NetUtil;
import java.net.InetAddress;
import java.net.URI;
import java.util.Arrays;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import java.util.stream.Collectors;
import org.springframework.core.env.Environment;
import org.springframework.stereotype.Component;

/** Origin AND address checks. Configuration belongs to deployment administrators. */
@Component
public class ModelEndpointPolicy {
    private final Set<String> origins;
    private final Set<String> publicOrigins;
    private final List<String> networks;
    private final Set<String> denied;
    private final boolean allowLoopbackForTests;

    @org.springframework.beans.factory.annotation.Autowired
    public ModelEndpointPolicy(Environment environment, ObjectMapper json) {
        this(read(environment, json, "MODEL_ALLOWED_ORIGINS"), read(environment, json, "MODEL_PUBLIC_DEMO_ORIGINS"),
                read(environment, json, "MODEL_ALLOWED_CIDRS"), read(environment, json, "MODEL_DENIED_ADDRESSES"), false);
        String clusterIp = environment.getProperty("KUBERNETES_SERVICE_HOST");
        if (clusterIp != null) denied.add(clusterIp);
    }

    ModelEndpointPolicy(List<String> origins, List<String> publicOrigins, List<String> networks, List<String> denied, boolean testLoopback) {
        this.origins = origins.stream().map(ModelEndpointPolicy::origin).collect(Collectors.toSet());
        this.publicOrigins = publicOrigins.stream().map(ModelEndpointPolicy::origin).collect(Collectors.toSet());
        if (!this.origins.containsAll(this.publicOrigins)) throw new IllegalArgumentException("Demo origins must also be approved");
        this.networks = List.copyOf(networks);
        this.denied = new java.util.HashSet<>(denied);
        this.allowLoopbackForTests = testLoopback;
        for (String network : networks) networkContains(network, new byte[4]); // validate config eagerly
    }

    private static List<String> read(Environment environment, ObjectMapper json, String name) {
        try {
            return json.readValue(environment.getProperty(name, "[]"), new TypeReference<List<String>>() {});
        } catch (Exception exception) {
            throw new IllegalArgumentException("Invalid model endpoint policy configuration");
        }
    }

    public static URI parse(String endpoint) {
        try {
            URI uri = URI.create(endpoint);
            if (endpoint.chars().anyMatch(c -> c <= 32) || endpoint.contains("\\") || uri.getHost() == null
                    || !Set.of("http", "https").contains(uri.getScheme()) || uri.getUserInfo() != null
                    || uri.getRawQuery() != null || uri.getRawFragment() != null || uri.getRawAuthority().contains("%")
                    || uri.getHost().endsWith(".") || uri.getPort() == 0 || uri.getPort() > 65535) throw denied();
            return uri;
        } catch (Exception exception) {
            throw denied();
        }
    }

    public static String origin(String endpoint) {
        URI uri = parse(endpoint);
        return uri.getScheme() + "://" + uri.getHost().toLowerCase(Locale.ROOT) + ":" + port(uri);
    }

    public static int port(URI uri) {
        return uri.getPort() == -1 ? (uri.getScheme().equals("https") ? 443 : 80) : uri.getPort();
    }

    public URI validate(String endpoint) {
        URI uri = parse(endpoint);
        String host = uri.getHost().toLowerCase(Locale.ROOT);
        if (!origins.contains(origin(endpoint)) || Set.of("metadata", "metadata.google.internal", "kubernetes", "kubernetes.default").contains(host)
                || host.startsWith("kubernetes.default.svc")) throw denied();
        return uri;
    }

    public record Approved(URI uri, List<InetAddress> addresses) {}

    public Approved resolve(String endpoint) {
        URI uri = validate(endpoint);
        try {
            List<InetAddress> addresses = Arrays.asList(InetAddress.getAllByName(uri.getHost()));
            if (addresses.isEmpty()) throw denied();
            for (InetAddress address : addresses) validateAddress(address, origin(endpoint));
            return new Approved(uri, List.copyOf(addresses));
        } catch (java.net.UnknownHostException exception) {
            throw denied();
        }
    }

    void validateAddress(InetAddress address, String origin) {
        String value = address.getHostAddress();
        if (denied.contains(value) || Set.of("100.100.100.200", "168.63.129.16").contains(value)
                || address.isAnyLocalAddress() || address.isLinkLocalAddress() || address.isMulticastAddress()
                || (address.isLoopbackAddress() && !allowLoopbackForTests)) throw denied();
        if (address.isLoopbackAddress() && allowLoopbackForTests) return;
        byte[] bytes = address.getAddress();
        boolean privateAddress = address.isSiteLocalAddress() || (bytes.length == 16 && (bytes[0] & 0xfe) == 0xfc);
        if (privateAddress) {
            if (networks.stream().noneMatch(network -> networkContains(network, bytes))) throw denied();
        } else if (!publicOrigins.contains(origin) || !origin.startsWith("https://")) {
            throw denied();
        }
    }

    private static boolean networkContains(String cidr, byte[] address) {
        try {
            String[] parts = cidr.split("/", -1);
            if (parts.length != 2) throw denied();
            byte[] network = NetUtil.createByteArrayFromIpAddressString(parts[0]);
            int prefix = Integer.parseInt(parts[1]);
            if (network == null || prefix < 0 || prefix > network.length * 8) throw denied();
            // Require canonical subnet definitions (no host bits).
            for (int bit = prefix; bit < network.length * 8; bit++) {
                if ((network[bit / 8] & (1 << (7 - bit % 8))) != 0) throw denied();
            }
            if (network.length != address.length) return false;
            for (int bit = 0; bit < prefix; bit++) {
                int mask = 1 << (7 - bit % 8);
                if ((network[bit / 8] & mask) != (address[bit / 8] & mask)) return false;
            }
            return true;
        } catch (RuntimeException exception) {
            throw new IllegalArgumentException("Invalid model address range configuration");
        }
    }

    public static final class EndpointDeniedException extends IllegalArgumentException {
        public EndpointDeniedException() { super("Model endpoint is not approved by the platform administrator"); }
    }

    public static IllegalArgumentException denied() {
        return new EndpointDeniedException();
    }
}
