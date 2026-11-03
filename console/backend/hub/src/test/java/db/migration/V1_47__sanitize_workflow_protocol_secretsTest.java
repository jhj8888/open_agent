package db.migration;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

import com.alibaba.fastjson2.JSON;
import com.alibaba.fastjson2.JSONObject;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Statement;
import java.sql.Timestamp;
import org.flywaydb.core.api.migration.Context;
import org.junit.jupiter.api.Test;

class V1_47__sanitize_workflow_protocol_secretsTest {

    @Test
    void sanitizesAllProtocolColumnsAndPreservesInvalidHistoricalJson() throws Exception {
        try (Connection connection = DriverManager.getConnection(
                "jdbc:h2:mem:workflow_protocol_sanitization;MODE=MySQL;DB_CLOSE_DELAY=-1")) {
            createTables(connection);
            insertRows(connection);
            Context context = mock(Context.class);
            when(context.getConnection()).thenReturn(connection);

            new V1_47__sanitize_workflow_protocol_secrets().migrate(context);

            assertBusinessProtocolSanitized(queryString(
                    connection, "SELECT data FROM workflow WHERE id = 1"));
            assertBusinessProtocolSanitized(queryString(
                    connection, "SELECT published_data FROM workflow WHERE id = 1"));
            assertThat(queryString(connection, "SELECT bak FROM workflow WHERE id = 1"))
                    .isEqualTo("{invalid-sandbox-secret");
            assertBusinessProtocolSanitized(queryString(
                    connection, "SELECT data FROM workflow_version WHERE id = 1"));
            assertThat(queryString(connection, "SELECT sys_data FROM workflow_version WHERE id = 1"))
                    .isNull();
            assertSystemProtocolSanitized(queryString(
                    connection, "SELECT sys_data FROM workflow_version WHERE id = 2"));
            assertBusinessProtocolSanitized(queryString(
                    connection, "SELECT biz_protocol FROM flow_protocol_temp WHERE flow_id = 'flow-1'"));
            assertThat(queryString(
                    connection,
                    "SELECT sys_protocol FROM flow_protocol_temp WHERE flow_id = 'flow-1'"))
                    .isNull();
            assertBusinessProtocolSanitized(queryString(
                    connection, "SELECT data FROM workflow_comparison WHERE id = 1"));
        }
    }

    @Test
    void rereadsAndSanitizesConcurrentWorkflowValueWithoutOverwritingIt() throws Exception {
        try (Connection connection = DriverManager.getConnection(
                "jdbc:h2:mem:workflow_protocol_concurrent;MODE=MySQL;DB_CLOSE_DELAY=-1")) {
            createWorkflowTable(connection);
            String original = businessProtocol("ORIGINAL ", "old-secret");
            String concurrent = businessProtocol("concurrent  ", "new-secret");
            insertWorkflow(connection, 1L, original);

            V1_47__sanitize_workflow_protocol_secrets migration =
                    new V1_47__sanitize_workflow_protocol_secrets(
                            new V1_47__sanitize_workflow_protocol_secrets.MigrationTestHook() {
                                private boolean changed;

                                @Override
                                public void afterPageRead(Connection hookConnection, String table)
                                        throws SQLException {
                                    if (!changed && "workflow".equals(table)) {
                                        changed = true;
                                        updateWorkflowData(hookConnection, 1L, concurrent);
                                    }
                                }
                            });

            migration.migrate(context(connection));

            JSONObject migrated = JSON.parseObject(
                    queryString(connection, "SELECT data FROM workflow WHERE id = 1"));
            assertThat(migrated.getString("marker")).isEqualTo("concurrent  ");
            assertThat(migrated.getString("writerField")).isEqualTo("keep-concurrent  ");
            assertThat(migrated.toJSONString()).doesNotContain("sandbox", "new-secret");
        }
    }

    @Test
    void repeatedConcurrentWorkflowChangesFailMigrationWithoutOverwritingLatestValue()
            throws Exception {
        try (Connection connection = DriverManager.getConnection(
                "jdbc:h2:mem:workflow_protocol_conflict;MODE=MySQL;DB_CLOSE_DELAY=-1")) {
            createWorkflowTable(connection);
            insertWorkflow(connection, 1L, businessProtocol("initial", "initial-secret"));

            V1_47__sanitize_workflow_protocol_secrets migration =
                    new V1_47__sanitize_workflow_protocol_secrets(
                            new V1_47__sanitize_workflow_protocol_secrets.MigrationTestHook() {
                                @Override
                                public void beforeOptimisticUpdate(
                                        Connection hookConnection,
                                        String table,
                                        String key,
                                        int attempt)
                                        throws SQLException {
                                    if ("workflow".equals(table)) {
                                        updateWorkflowData(
                                                hookConnection,
                                                1L,
                                                businessProtocol(
                                                        "writer-" + attempt,
                                                        "writer-secret-" + attempt));
                                    }
                                }
                            });

            assertThatThrownBy(() -> migration.migrate(context(connection)))
                    .isInstanceOf(SQLException.class)
                    .hasMessageContaining("Concurrent modification")
                    .hasMessageContaining("workflow")
                    .hasMessageNotContaining("writer-secret");

            String latest = queryString(connection, "SELECT data FROM workflow WHERE id = 1");
            assertThat(JSON.parseObject(latest).getString("marker")).isEqualTo("writer-3");
            assertThat(latest).contains("writer-secret-3");
        }
    }

    @Test
    void flowProtocolTempRetriesLatestExactValueAndAcceptsIdenticalDuplicates()
            throws Exception {
        try (Connection connection = DriverManager.getConnection(
                "jdbc:h2:mem:flow_protocol_temp_concurrent;MODE=MySQL;DB_CLOSE_DELAY=-1")) {
            createFlowProtocolTempTable(connection);
            Timestamp createdTime = Timestamp.valueOf("2026-08-24 12:00:00");
            String original = businessProtocol("TEMP-OLD ", "temp-old-secret");
            String concurrent = businessProtocol("temp-new  ", "temp-new-secret");
            insertTemp(connection, "flow-1", createdTime, original);

            V1_47__sanitize_workflow_protocol_secrets migration =
                    new V1_47__sanitize_workflow_protocol_secrets(
                            new V1_47__sanitize_workflow_protocol_secrets.MigrationTestHook() {
                                private boolean changed;

                                @Override
                                public void afterPageRead(Connection hookConnection, String table)
                                        throws SQLException {
                                    if (!changed && "flow_protocol_temp".equals(table)) {
                                        changed = true;
                                        try (PreparedStatement update = hookConnection.prepareStatement(
                                                "UPDATE flow_protocol_temp SET biz_protocol = ? "
                                                        + "WHERE flow_id = 'flow-1'")) {
                                            update.setString(1, concurrent);
                                            assertThat(update.executeUpdate()).isEqualTo(1);
                                        }
                                    }
                                }
                            });

            migration.migrate(context(connection));
            String migrated = queryString(
                    connection,
                    "SELECT biz_protocol FROM flow_protocol_temp WHERE flow_id = 'flow-1'");
            assertThat(JSON.parseObject(migrated).getString("marker")).isEqualTo("temp-new  ");
            assertThat(migrated).doesNotContain("sandbox", "temp-new-secret");

            // A second run with byte-identical duplicate rows may update both rows at once.
            String duplicate = businessProtocol("duplicate", "duplicate-secret");
            insertTemp(connection, "flow-2", createdTime, duplicate);
            insertTemp(connection, "flow-2", createdTime, duplicate);
            new V1_47__sanitize_workflow_protocol_secrets().migrate(context(connection));
            try (Statement statement = connection.createStatement();
                    ResultSet rows = statement.executeQuery(
                            "SELECT biz_protocol FROM flow_protocol_temp "
                                    + "WHERE flow_id = 'flow-2'")) {
                int count = 0;
                while (rows.next()) {
                    count++;
                    assertThat(rows.getString(1)).doesNotContain("sandbox", "duplicate-secret");
                }
                assertThat(count).isEqualTo(2);
            }
        }
    }

    @Test
    void keysetPaginationSanitizesMoreThanOnePage() throws Exception {
        try (Connection connection = DriverManager.getConnection(
                "jdbc:h2:mem:workflow_protocol_keyset;MODE=MySQL;DB_CLOSE_DELAY=-1")) {
            createWorkflowTable(connection);
            for (long id = 1; id <= 205; id++) {
                insertWorkflow(
                        connection, id, businessProtocol("row-" + id, "secret-" + id));
            }

            new V1_47__sanitize_workflow_protocol_secrets().migrate(context(connection));

            try (Statement statement = connection.createStatement();
                    ResultSet resultSet = statement.executeQuery(
                            "SELECT COUNT(*) FROM workflow WHERE data LIKE '%sandbox%'")) {
                assertThat(resultSet.next()).isTrue();
                assertThat(resultSet.getInt(1)).isZero();
            }
        }
    }

    private static Context context(Connection connection) {
        Context context = mock(Context.class);
        when(context.getConnection()).thenReturn(connection);
        return context;
    }

    private static String businessProtocol(String marker, String sandboxSecret) {
        JSONObject sandbox = new JSONObject();
        sandbox.put("apiKey", sandboxSecret);
        JSONObject node = new JSONObject();
        node.put("sandbox", sandbox);
        JSONObject protocol = new JSONObject();
        protocol.put("node", node);
        protocol.put("marker", marker);
        protocol.put("writerField", "keep-" + marker);
        protocol.put("apiKey", "model-key");
        return protocol.toJSONString();
    }

    private static void createWorkflowTable(Connection connection) throws SQLException {
        try (Statement statement = connection.createStatement()) {
            statement.execute("CREATE TABLE workflow ("
                    + "id BIGINT PRIMARY KEY, data MEDIUMTEXT, "
                    + "published_data MEDIUMTEXT, bak MEDIUMTEXT)");
        }
    }

    private static void createFlowProtocolTempTable(Connection connection) throws SQLException {
        try (Statement statement = connection.createStatement()) {
            statement.execute("CREATE TABLE flow_protocol_temp ("
                    + "flow_id VARCHAR(255), created_time TIMESTAMP, "
                    + "biz_protocol MEDIUMTEXT, sys_protocol MEDIUMTEXT)");
        }
    }

    private static void insertWorkflow(Connection connection, long id, String data)
            throws SQLException {
        try (PreparedStatement insert = connection.prepareStatement(
                "INSERT INTO workflow (id, data, published_data, bak) VALUES (?, ?, NULL, NULL)")) {
            insert.setLong(1, id);
            insert.setString(2, data);
            assertThat(insert.executeUpdate()).isEqualTo(1);
        }
    }

    private static void updateWorkflowData(Connection connection, long id, String data)
            throws SQLException {
        try (PreparedStatement update = connection.prepareStatement(
                "UPDATE workflow SET data = ? WHERE id = ?")) {
            update.setString(1, data);
            update.setLong(2, id);
            assertThat(update.executeUpdate()).isEqualTo(1);
        }
    }

    private static void insertTemp(
            Connection connection, String flowId, Timestamp createdTime, String businessProtocol)
            throws SQLException {
        try (PreparedStatement insert = connection.prepareStatement(
                "INSERT INTO flow_protocol_temp "
                        + "(flow_id, created_time, biz_protocol, sys_protocol) "
                        + "VALUES (?, ?, ?, NULL)")) {
            insert.setString(1, flowId);
            insert.setTimestamp(2, createdTime);
            insert.setString(3, businessProtocol);
            assertThat(insert.executeUpdate()).isEqualTo(1);
        }
    }

    private static void createTables(Connection connection) throws Exception {
        try (Statement statement = connection.createStatement()) {
            statement.execute("CREATE TABLE workflow ("
                    + "id BIGINT PRIMARY KEY, data MEDIUMTEXT, published_data MEDIUMTEXT, bak MEDIUMTEXT)");
            statement.execute("CREATE TABLE workflow_version ("
                    + "id BIGINT PRIMARY KEY, data MEDIUMTEXT, sys_data MEDIUMTEXT)");
            statement.execute("CREATE TABLE flow_protocol_temp ("
                    + "flow_id VARCHAR(255), created_time TIMESTAMP, "
                    + "biz_protocol MEDIUMTEXT, sys_protocol MEDIUMTEXT)");
            statement.execute("CREATE TABLE workflow_comparison (id BIGINT PRIMARY KEY, data MEDIUMTEXT)");
        }
    }

    private static void insertRows(Connection connection) throws Exception {
        String business = "{\"node\":{\"sandbox\":{\"apiKey\":\"secret\"},"
                + "\"apiKey\":\"model-key\"}}";
        String legacy = "{\"artifact_upload_token\":\"secret\",\"apiKey\":\"model-key\"}";
        String system = "{\"node\":{\"sandbox\":{\"enabled\":true,\"uid\":\"user-1\","
                + "\"workflow_id\":\"flow-1\",\"apiKey\":\"secret\","
                + "\"runtimeConfigUrl\":\"http://internal/runtime\"}}}";
        try (PreparedStatement insert = connection.prepareStatement(
                "INSERT INTO workflow (id, data, published_data, bak) VALUES (1, ?, ?, ?)")) {
            insert.setString(1, business);
            insert.setString(2, legacy);
            insert.setString(3, "{invalid-sandbox-secret");
            insert.executeUpdate();
        }
        try (PreparedStatement insert = connection.prepareStatement(
                "INSERT INTO workflow_version (id, data, sys_data) VALUES (?, ?, ?)")) {
            insert.setLong(1, 1L);
            insert.setString(2, business);
            insert.setString(3, null);
            insert.executeUpdate();
            insert.setLong(1, 2L);
            insert.setString(2, "{\"apiKey\":\"model-key\"}");
            insert.setString(3, system);
            insert.executeUpdate();
        }
        try (PreparedStatement insert = connection.prepareStatement(
                "INSERT INTO flow_protocol_temp "
                        + "(flow_id, created_time, biz_protocol, sys_protocol) "
                        + "VALUES ('flow-1', CURRENT_TIMESTAMP, ?, NULL)")) {
            insert.setString(1, business);
            insert.executeUpdate();
        }
        try (PreparedStatement insert = connection.prepareStatement(
                "INSERT INTO workflow_comparison (id, data) VALUES (1, ?)")) {
            insert.setString(1, business);
            insert.executeUpdate();
        }
    }

    private static String queryString(Connection connection, String sql) throws Exception {
        try (Statement statement = connection.createStatement();
                ResultSet resultSet = statement.executeQuery(sql)) {
            assertThat(resultSet.next()).isTrue();
            return resultSet.getString(1);
        }
    }

    private static void assertBusinessProtocolSanitized(String protocol) {
        assertThat(protocol).doesNotContain("sandbox", "secret");
        assertThat(protocol).contains("model-key");
    }

    private static void assertSystemProtocolSanitized(String protocol) {
        JSONObject sandbox = JSON.parseObject(protocol)
                .getJSONObject("node")
                .getJSONObject("sandbox");
        assertThat(sandbox)
                .containsEntry("enabled", true)
                .containsEntry("uid", "user-1")
                .containsEntry("workflow_id", "flow-1")
                .doesNotContainKeys("apiKey", "runtimeConfigUrl");
        assertThat(protocol).doesNotContain("secret", "internal/runtime");
    }
}
