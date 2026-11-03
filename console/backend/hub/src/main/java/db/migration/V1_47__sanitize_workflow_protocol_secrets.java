package db.migration;

import com.iflytek.astron.console.commons.util.WorkflowProtocolSanitizer;
import com.iflytek.astron.console.commons.util.WorkflowProtocolSanitizer.SanitizationResult;
import org.flywaydb.core.api.migration.BaseJavaMigration;
import org.flywaydb.core.api.migration.Context;

import java.sql.Connection;
import java.sql.DatabaseMetaData;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Timestamp;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;

/** Removes historical server-derived sandbox data and credentials from persisted workflow JSON. */
public class V1_47__sanitize_workflow_protocol_secrets extends BaseJavaMigration {

    private static final int BATCH_SIZE = 100;
    private static final int MAX_CONFLICT_RETRIES = 3;
    private static final String UPDATE_WORKFLOW_MYSQL =
            "UPDATE workflow SET data = ?, published_data = ?, bak = ? WHERE id = ? AND "
                    + "BINARY data <=> BINARY ? AND BINARY published_data <=> BINARY ? AND "
                    + "BINARY bak <=> BINARY ?";
    private static final String UPDATE_WORKFLOW_H2 =
            "UPDATE workflow SET data = ?, published_data = ?, bak = ? WHERE id = ? AND "
                    + "CAST(data AS BINARY VARYING) IS NOT DISTINCT FROM "
                    + "CAST(? AS BINARY VARYING) AND "
                    + "CAST(published_data AS BINARY VARYING) IS NOT DISTINCT FROM "
                    + "CAST(? AS BINARY VARYING) AND "
                    + "CAST(bak AS BINARY VARYING) IS NOT DISTINCT FROM "
                    + "CAST(? AS BINARY VARYING)";
    private static final String UPDATE_WORKFLOW_VERSION_MYSQL =
            "UPDATE workflow_version SET data = ?, sys_data = ? WHERE id = ? AND "
                    + "BINARY data <=> BINARY ? AND BINARY sys_data <=> BINARY ?";
    private static final String UPDATE_WORKFLOW_VERSION_H2 =
            "UPDATE workflow_version SET data = ?, sys_data = ? WHERE id = ? AND "
                    + "CAST(data AS BINARY VARYING) IS NOT DISTINCT FROM "
                    + "CAST(? AS BINARY VARYING) AND "
                    + "CAST(sys_data AS BINARY VARYING) IS NOT DISTINCT FROM "
                    + "CAST(? AS BINARY VARYING)";
    private static final String UPDATE_WORKFLOW_COMPARISON_MYSQL =
            "UPDATE workflow_comparison SET data = ? WHERE id = ? AND "
                    + "BINARY data <=> BINARY ?";
    private static final String UPDATE_WORKFLOW_COMPARISON_H2 =
            "UPDATE workflow_comparison SET data = ? WHERE id = ? AND "
                    + "CAST(data AS BINARY VARYING) IS NOT DISTINCT FROM "
                    + "CAST(? AS BINARY VARYING)";
    private static final String UPDATE_FLOW_PROTOCOL_TEMP_MYSQL =
            "UPDATE flow_protocol_temp SET biz_protocol = ?, sys_protocol = ? WHERE "
                    + "BINARY flow_id <=> BINARY ? AND created_time = ? AND "
                    + "BINARY biz_protocol <=> BINARY ? AND BINARY sys_protocol <=> BINARY ?";
    private static final String UPDATE_FLOW_PROTOCOL_TEMP_H2 =
            "UPDATE flow_protocol_temp SET biz_protocol = ?, sys_protocol = ? WHERE "
                    + "CAST(flow_id AS BINARY VARYING) IS NOT DISTINCT FROM "
                    + "CAST(? AS BINARY VARYING) AND created_time = ? AND "
                    + "CAST(biz_protocol AS BINARY VARYING) IS NOT DISTINCT FROM "
                    + "CAST(? AS BINARY VARYING) AND "
                    + "CAST(sys_protocol AS BINARY VARYING) IS NOT DISTINCT FROM "
                    + "CAST(? AS BINARY VARYING)";
    private static final String REREAD_FLOW_PROTOCOL_TEMP_MYSQL =
            "SELECT flow_id, created_time, biz_protocol, sys_protocol "
                    + "FROM flow_protocol_temp WHERE BINARY flow_id <=> BINARY ? "
                    + "AND created_time = ? FOR UPDATE";
    private static final String REREAD_FLOW_PROTOCOL_TEMP_H2 =
            "SELECT flow_id, created_time, biz_protocol, sys_protocol "
                    + "FROM flow_protocol_temp WHERE "
                    + "CAST(flow_id AS BINARY VARYING) IS NOT DISTINCT FROM "
                    + "CAST(? AS BINARY VARYING) AND created_time = ? FOR UPDATE";
    private final MigrationTestHook testHook;

    private static final Set<String> WORKFLOW_COLUMNS =
            Set.of("id", "data", "published_data", "bak");
    private static final Set<String> WORKFLOW_VERSION_COLUMNS =
            Set.of("id", "data", "sys_data");
    private static final Set<String> FLOW_PROTOCOL_TEMP_COLUMNS =
            Set.of("flow_id", "created_time", "biz_protocol", "sys_protocol");
    private static final Set<String> WORKFLOW_COMPARISON_COLUMNS = Set.of("id", "data");

    public V1_47__sanitize_workflow_protocol_secrets() {
        this(MigrationTestHook.NONE);
    }

    V1_47__sanitize_workflow_protocol_secrets(MigrationTestHook testHook) {
        this.testHook = testHook;
    }

    @Override
    public void migrate(Context context) throws Exception {
        Connection connection = context.getConnection();
        Map<String, Set<String>> schema = inspectSchema(connection);

        if (hasColumns(schema, "workflow", WORKFLOW_COLUMNS)) {
            sanitizeWorkflow(connection);
        }
        if (hasColumns(schema, "workflow_version", WORKFLOW_VERSION_COLUMNS)) {
            sanitizeWorkflowVersion(connection);
        }
        if (hasColumns(schema, "flow_protocol_temp", FLOW_PROTOCOL_TEMP_COLUMNS)) {
            sanitizeFlowProtocolTemp(connection);
        }
        if (hasColumns(schema, "workflow_comparison", WORKFLOW_COMPARISON_COLUMNS)) {
            sanitizeWorkflowComparison(connection);
        }
    }

    private void sanitizeWorkflow(Connection connection) throws SQLException {
        long lastId = Long.MIN_VALUE;
        while (true) {
            List<WorkflowRow> rows = new ArrayList<>(BATCH_SIZE);
            try (PreparedStatement select = connection.prepareStatement(
                    "SELECT id, data, published_data, bak FROM workflow "
                            + "WHERE id > ? ORDER BY id LIMIT ?")) {
                select.setLong(1, lastId);
                select.setInt(2, BATCH_SIZE);
                select.setFetchSize(BATCH_SIZE);
                try (ResultSet resultSet = select.executeQuery()) {
                    while (resultSet.next()) {
                        rows.add(new WorkflowRow(
                                resultSet.getLong("id"),
                                resultSet.getString("data"),
                                resultSet.getString("published_data"),
                                resultSet.getString("bak")));
                    }
                }
            }
            if (rows.isEmpty()) {
                return;
            }
            afterPageRead(connection, "workflow");
            for (WorkflowRow row : rows) {
                updateWorkflowWithRetry(connection, row);
            }
            lastId = rows.get(rows.size() - 1).id();
        }
    }

    private void sanitizeWorkflowVersion(Connection connection) throws SQLException {
        long lastId = Long.MIN_VALUE;
        while (true) {
            List<VersionRow> rows = new ArrayList<>(BATCH_SIZE);
            try (PreparedStatement select = connection.prepareStatement(
                    "SELECT id, data, sys_data FROM workflow_version "
                            + "WHERE id > ? ORDER BY id LIMIT ?")) {
                select.setLong(1, lastId);
                select.setInt(2, BATCH_SIZE);
                select.setFetchSize(BATCH_SIZE);
                try (ResultSet resultSet = select.executeQuery()) {
                    while (resultSet.next()) {
                        rows.add(new VersionRow(
                                resultSet.getLong("id"),
                                resultSet.getString("data"),
                                resultSet.getString("sys_data")));
                    }
                }
            }
            if (rows.isEmpty()) {
                return;
            }
            afterPageRead(connection, "workflow_version");
            for (VersionRow row : rows) {
                updateWorkflowVersionWithRetry(connection, row);
            }
            lastId = rows.get(rows.size() - 1).id();
        }
    }

    private void sanitizeFlowProtocolTemp(Connection connection) throws SQLException {
        long offset = 0;
        while (true) {
            List<TempRow> rows = new ArrayList<>(BATCH_SIZE);
            try (PreparedStatement select = connection.prepareStatement(
                    "SELECT flow_id, created_time, biz_protocol, sys_protocol "
                            + "FROM flow_protocol_temp ORDER BY flow_id, created_time "
                            + "LIMIT ? OFFSET ? FOR UPDATE")) {
                select.setInt(1, BATCH_SIZE);
                select.setLong(2, offset);
                select.setFetchSize(BATCH_SIZE);
                try (ResultSet resultSet = select.executeQuery()) {
                    while (resultSet.next()) {
                        rows.add(new TempRow(
                                resultSet.getString("flow_id"),
                                resultSet.getTimestamp("created_time"),
                                resultSet.getString("biz_protocol"),
                                resultSet.getString("sys_protocol")));
                    }
                }
            }
            if (rows.isEmpty()) {
                return;
            }
            afterPageRead(connection, "flow_protocol_temp");
            for (TempRow row : rows) {
                updateFlowProtocolTempWithRetry(connection, row);
            }
            offset += rows.size();
        }
    }

    private void sanitizeWorkflowComparison(Connection connection) throws SQLException {
        long lastId = Long.MIN_VALUE;
        while (true) {
            List<ComparisonRow> rows = new ArrayList<>(BATCH_SIZE);
            try (PreparedStatement select = connection.prepareStatement(
                    "SELECT id, data FROM workflow_comparison WHERE id > ? ORDER BY id LIMIT ?")) {
                select.setLong(1, lastId);
                select.setInt(2, BATCH_SIZE);
                select.setFetchSize(BATCH_SIZE);
                try (ResultSet resultSet = select.executeQuery()) {
                    while (resultSet.next()) {
                        rows.add(new ComparisonRow(
                                resultSet.getLong("id"), resultSet.getString("data")));
                    }
                }
            }
            if (rows.isEmpty()) {
                return;
            }
            afterPageRead(connection, "workflow_comparison");
            for (ComparisonRow row : rows) {
                updateWorkflowComparisonWithRetry(connection, row);
            }
            lastId = rows.get(rows.size() - 1).id();
        }
    }

    private void updateWorkflowWithRetry(Connection connection, WorkflowRow initial)
            throws SQLException {
        WorkflowRow row = initial;
        for (int attempt = 0; attempt <= MAX_CONFLICT_RETRIES; attempt++) {
            SanitizedColumns columns = sanitize(row.data(), row.publishedData(), row.bak());
            if (!columns.changed()) {
                return;
            }
            beforeOptimisticUpdate(connection, "workflow", Long.toString(row.id()), attempt);
            int count;
            try (PreparedStatement update = prepareExactTextStatement(
                    connection, ExactTextStatement.UPDATE_WORKFLOW)) {
                update.setString(1, columns.values().get(0));
                update.setString(2, columns.values().get(1));
                update.setString(3, columns.values().get(2));
                update.setLong(4, row.id());
                update.setString(5, row.data());
                update.setString(6, row.publishedData());
                update.setString(7, row.bak());
                count = update.executeUpdate();
            }
            if (count == 1) {
                return;
            }
            if (count != 0) {
                throw unexpectedUpdateCount("workflow", Long.toString(row.id()), count);
            }
            if (attempt == MAX_CONFLICT_RETRIES) {
                throw concurrentUpdateFailure("workflow", Long.toString(row.id()));
            }
            row = readWorkflow(connection, row.id());
        }
    }

    private void updateWorkflowVersionWithRetry(Connection connection, VersionRow initial)
            throws SQLException {
        VersionRow row = initial;
        for (int attempt = 0; attempt <= MAX_CONFLICT_RETRIES; attempt++) {
            SanitizedColumns columns = sanitizeBusinessAndSystem(row.data(), row.sysData());
            if (!columns.changed()) {
                return;
            }
            beforeOptimisticUpdate(
                    connection, "workflow_version", Long.toString(row.id()), attempt);
            int count;
            try (PreparedStatement update = prepareExactTextStatement(
                    connection, ExactTextStatement.UPDATE_WORKFLOW_VERSION)) {
                update.setString(1, columns.values().get(0));
                update.setString(2, columns.values().get(1));
                update.setLong(3, row.id());
                update.setString(4, row.data());
                update.setString(5, row.sysData());
                count = update.executeUpdate();
            }
            if (count == 1) {
                return;
            }
            if (count != 0) {
                throw unexpectedUpdateCount(
                        "workflow_version", Long.toString(row.id()), count);
            }
            if (attempt == MAX_CONFLICT_RETRIES) {
                throw concurrentUpdateFailure("workflow_version", Long.toString(row.id()));
            }
            row = readWorkflowVersion(connection, row.id());
        }
    }

    private void updateWorkflowComparisonWithRetry(
            Connection connection, ComparisonRow initial) throws SQLException {
        ComparisonRow row = initial;
        for (int attempt = 0; attempt <= MAX_CONFLICT_RETRIES; attempt++) {
            SanitizedColumns columns = sanitize(row.data());
            if (!columns.changed()) {
                return;
            }
            beforeOptimisticUpdate(
                    connection, "workflow_comparison", Long.toString(row.id()), attempt);
            int count;
            try (PreparedStatement update = prepareExactTextStatement(
                    connection, ExactTextStatement.UPDATE_WORKFLOW_COMPARISON)) {
                update.setString(1, columns.values().get(0));
                update.setLong(2, row.id());
                update.setString(3, row.data());
                count = update.executeUpdate();
            }
            if (count == 1) {
                return;
            }
            if (count != 0) {
                throw unexpectedUpdateCount(
                        "workflow_comparison", Long.toString(row.id()), count);
            }
            if (attempt == MAX_CONFLICT_RETRIES) {
                throw concurrentUpdateFailure(
                        "workflow_comparison", Long.toString(row.id()));
            }
            row = readWorkflowComparison(connection, row.id());
        }
    }

    private void updateFlowProtocolTempWithRetry(Connection connection, TempRow initial)
            throws SQLException {
        TempRow row = initial;
        String key = row.flowId() + "/" + row.createdTime();
        for (int attempt = 0; attempt <= MAX_CONFLICT_RETRIES; attempt++) {
            SanitizedColumns columns =
                    sanitizeBusinessAndSystem(row.bizProtocol(), row.sysProtocol());
            if (!columns.changed()) {
                return;
            }
            beforeOptimisticUpdate(connection, "flow_protocol_temp", key, attempt);
            int count;
            try (PreparedStatement update = prepareExactTextStatement(
                    connection, ExactTextStatement.UPDATE_FLOW_PROTOCOL_TEMP)) {
                update.setString(1, columns.values().get(0));
                update.setString(2, columns.values().get(1));
                update.setString(3, row.flowId());
                update.setTimestamp(4, row.createdTime());
                update.setString(5, row.bizProtocol());
                update.setString(6, row.sysProtocol());
                count = update.executeUpdate();
            }
            // flow_protocol_temp has no primary key. Multiple byte-identical rows with the
            // same logical key are safely sanitized by the same exact-value update.
            if (count > 0) {
                return;
            }
            if (attempt == MAX_CONFLICT_RETRIES) {
                throw concurrentUpdateFailure("flow_protocol_temp", key);
            }
            row = rereadDirtyTempRow(connection, row.flowId(), row.createdTime());
            if (row == null) {
                return;
            }
        }
    }

    private static WorkflowRow readWorkflow(Connection connection, long id) throws SQLException {
        try (PreparedStatement select = connection.prepareStatement(
                "SELECT id, data, published_data, bak FROM workflow WHERE id = ?")) {
            select.setLong(1, id);
            try (ResultSet resultSet = select.executeQuery()) {
                if (!resultSet.next()) {
                    throw concurrentUpdateFailure("workflow", Long.toString(id));
                }
                return new WorkflowRow(
                        resultSet.getLong("id"),
                        resultSet.getString("data"),
                        resultSet.getString("published_data"),
                        resultSet.getString("bak"));
            }
        }
    }

    private static VersionRow readWorkflowVersion(Connection connection, long id)
            throws SQLException {
        try (PreparedStatement select = connection.prepareStatement(
                "SELECT id, data, sys_data FROM workflow_version WHERE id = ?")) {
            select.setLong(1, id);
            try (ResultSet resultSet = select.executeQuery()) {
                if (!resultSet.next()) {
                    throw concurrentUpdateFailure("workflow_version", Long.toString(id));
                }
                return new VersionRow(
                        resultSet.getLong("id"),
                        resultSet.getString("data"),
                        resultSet.getString("sys_data"));
            }
        }
    }

    private static ComparisonRow readWorkflowComparison(Connection connection, long id)
            throws SQLException {
        try (PreparedStatement select = connection.prepareStatement(
                "SELECT id, data FROM workflow_comparison WHERE id = ?")) {
            select.setLong(1, id);
            try (ResultSet resultSet = select.executeQuery()) {
                if (!resultSet.next()) {
                    throw concurrentUpdateFailure("workflow_comparison", Long.toString(id));
                }
                return new ComparisonRow(resultSet.getLong("id"), resultSet.getString("data"));
            }
        }
    }

    private static TempRow rereadDirtyTempRow(
            Connection connection, String flowId, Timestamp createdTime) throws SQLException {
        try (PreparedStatement select = prepareExactTextStatement(
                connection, ExactTextStatement.REREAD_FLOW_PROTOCOL_TEMP)) {
            select.setString(1, flowId);
            select.setTimestamp(2, createdTime);
            try (ResultSet resultSet = select.executeQuery()) {
                boolean found = false;
                while (resultSet.next()) {
                    found = true;
                    TempRow candidate = new TempRow(
                            resultSet.getString("flow_id"),
                            resultSet.getTimestamp("created_time"),
                            resultSet.getString("biz_protocol"),
                            resultSet.getString("sys_protocol"));
                    SanitizedColumns columns = sanitizeBusinessAndSystem(
                            candidate.bizProtocol(), candidate.sysProtocol());
                    if (columns.changed()) {
                        return candidate;
                    }
                }
                if (!found) {
                    throw concurrentUpdateFailure(
                            "flow_protocol_temp", flowId + "/" + createdTime);
                }
                return null;
            }
        }
    }

    private static PreparedStatement prepareExactTextStatement(
            Connection connection, ExactTextStatement statement) throws SQLException {
        String productName = connection.getMetaData().getDatabaseProductName();
        if ("H2".equalsIgnoreCase(productName)) {
            return switch (statement) {
                case UPDATE_WORKFLOW -> connection.prepareStatement(UPDATE_WORKFLOW_H2);
                case UPDATE_WORKFLOW_VERSION ->
                    connection.prepareStatement(UPDATE_WORKFLOW_VERSION_H2);
                case UPDATE_WORKFLOW_COMPARISON ->
                    connection.prepareStatement(UPDATE_WORKFLOW_COMPARISON_H2);
                case UPDATE_FLOW_PROTOCOL_TEMP ->
                    connection.prepareStatement(UPDATE_FLOW_PROTOCOL_TEMP_H2);
                case REREAD_FLOW_PROTOCOL_TEMP ->
                    connection.prepareStatement(REREAD_FLOW_PROTOCOL_TEMP_H2);
            };
        }
        return switch (statement) {
            case UPDATE_WORKFLOW -> connection.prepareStatement(UPDATE_WORKFLOW_MYSQL);
            case UPDATE_WORKFLOW_VERSION ->
                connection.prepareStatement(UPDATE_WORKFLOW_VERSION_MYSQL);
            case UPDATE_WORKFLOW_COMPARISON ->
                connection.prepareStatement(UPDATE_WORKFLOW_COMPARISON_MYSQL);
            case UPDATE_FLOW_PROTOCOL_TEMP ->
                connection.prepareStatement(UPDATE_FLOW_PROTOCOL_TEMP_MYSQL);
            case REREAD_FLOW_PROTOCOL_TEMP ->
                connection.prepareStatement(REREAD_FLOW_PROTOCOL_TEMP_MYSQL);
        };
    }

    private enum ExactTextStatement {
        UPDATE_WORKFLOW,
        UPDATE_WORKFLOW_VERSION,
        UPDATE_WORKFLOW_COMPARISON,
        UPDATE_FLOW_PROTOCOL_TEMP,
        REREAD_FLOW_PROTOCOL_TEMP
    }

    private static SQLException unexpectedUpdateCount(String table, String key, int count) {
        return new SQLException(
                "Unexpected protocol sanitization update count for " + table + " key " + key
                        + ": " + count);
    }

    private static SQLException concurrentUpdateFailure(String table, String key) {
        return new SQLException(
                "Concurrent modification prevented protocol sanitization for " + table
                        + " key " + key);
    }

    private void afterPageRead(Connection connection, String table) throws SQLException {
        testHook.afterPageRead(connection, table);
    }

    private void beforeOptimisticUpdate(
            Connection connection, String table, String key, int attempt) throws SQLException {
        testHook.beforeOptimisticUpdate(connection, table, key, attempt);
    }

    private static SanitizedColumns sanitize(String... values) {
        boolean changed = false;
        List<String> sanitizedValues = new ArrayList<>(values.length);
        for (String value : values) {
            SanitizationResult result = WorkflowProtocolSanitizer.analyze(value);
            if (result.validJson() && result.changed()) {
                sanitizedValues.add(result.sanitizedJson());
                changed = true;
            } else {
                // Invalid historical JSON must not be overwritten. Entity response setters still
                // fail closed so the unchanged database value cannot be returned to a client.
                sanitizedValues.add(value);
            }
        }
        return new SanitizedColumns(changed, sanitizedValues);
    }

    private static SanitizedColumns sanitizeBusinessAndSystem(
            String businessProtocol, String systemProtocol) {
        SanitizationResult businessResult = WorkflowProtocolSanitizer.analyze(businessProtocol);
        SanitizationResult systemResult =
                WorkflowProtocolSanitizer.analyzeSystemProtocol(systemProtocol);
        boolean changed = (businessResult.validJson() && businessResult.changed())
                || (systemResult.validJson() && systemResult.changed());
        List<String> values = new ArrayList<>(2);
        values.add(migrationValue(businessProtocol, businessResult));
        values.add(migrationValue(systemProtocol, systemResult));
        return new SanitizedColumns(changed, values);
    }

    private static String migrationValue(String original, SanitizationResult result) {
        return result.validJson() && result.changed() ? result.sanitizedJson() : original;
    }

    private static Map<String, Set<String>> inspectSchema(Connection connection) throws SQLException {
        Map<String, Set<String>> schema = new HashMap<>();
        DatabaseMetaData metadata = connection.getMetaData();
        try (ResultSet columns = metadata.getColumns(connection.getCatalog(), null, "%", "%")) {
            while (columns.next()) {
                String tableName = columns.getString("TABLE_NAME").toLowerCase(Locale.ROOT);
                String columnName = columns.getString("COLUMN_NAME").toLowerCase(Locale.ROOT);
                schema.computeIfAbsent(tableName, ignored -> new HashSet<>()).add(columnName);
            }
        }
        return schema;
    }

    private static boolean hasColumns(
            Map<String, Set<String>> schema, String tableName, Set<String> columns) {
        return schema.getOrDefault(tableName, Set.of()).containsAll(columns);
    }

    private record WorkflowRow(long id, String data, String publishedData, String bak) {}

    private record VersionRow(long id, String data, String sysData) {}

    private record TempRow(
            String flowId,
            Timestamp createdTime,
            String bizProtocol,
            String sysProtocol) {}

    private record ComparisonRow(long id, String data) {}

    private record SanitizedColumns(boolean changed, List<String> values) {}

    interface MigrationTestHook {
        MigrationTestHook NONE = new MigrationTestHook() {};

        default void afterPageRead(Connection connection, String table) throws SQLException {}

        default void beforeOptimisticUpdate(
                Connection connection, String table, String key, int attempt) throws SQLException {}
    }
}
