package com.iflytek.astron.console.hub.platform.run;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.HexFormat;
import java.util.TreeSet;

/** Stable request fingerprints do not depend on JSON object property order. */
final class RunJson {
    private RunJson() {}
    static String encode(ObjectMapper mapper, JsonNode value, int maximumBytes) {
        if (value == null || !value.isObject()) throw new RunFailure("invalid_input");
        // Check depth and a lower bound on size before invoking the JSON serializer.
        String encoded = sorted(mapper, value, 0, new int[] {maximumBytes}).toString();
        if (encoded.getBytes(StandardCharsets.UTF_8).length > maximumBytes) throw new RunFailure("input_too_large");
        return encoded;
    }
    private static JsonNode sorted(ObjectMapper mapper, JsonNode node, int depth, int[] budget) {
        if (depth > 32) throw new RunFailure("input_too_deep");
        budget[0] -= node.isTextual() ? node.textValue().length() + 2 : 1;
        if (budget[0] < 0) throw new RunFailure("input_too_large");
        if (node.isObject()) {
            ObjectNode result = mapper.createObjectNode();
            TreeSet<String> names = new TreeSet<>();
            node.fieldNames().forEachRemaining(name -> {
                budget[0] -= name.length() + 3;
                if (budget[0] < 0) throw new RunFailure("input_too_large");
                names.add(name);
            });
            for (String name : names) result.set(name, sorted(mapper, node.get(name), depth + 1, budget));
            return result;
        }
        if (node.isArray()) {
            ArrayNode result = mapper.createArrayNode();
            node.forEach(child -> result.add(sorted(mapper, child, depth + 1, budget)));
            return result;
        }
        if (!(node.isTextual() || node.isNumber() || node.isBoolean() || node.isNull())) throw new RunFailure("invalid_input");
        return node;
    }
    static String hash(String text) {
        try {
            return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(text.getBytes(StandardCharsets.UTF_8)));
        } catch (java.security.NoSuchAlgorithmException impossible) { throw new IllegalStateException(impossible); }
    }
}
