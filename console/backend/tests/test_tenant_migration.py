"""Exercise V1.49 and the real authorization SELECT against MySQL, not an SQL mock.

Run from any directory with Python 3 and Docker. MYSQL_TEST_IMAGE must be preloaded
for an offline run. The ephemeral MySQL has no network, no host ports or mounts;
its empty test password is never used in a deployment configuration.
"""
import os
from pathlib import Path
import re
import subprocess
import time
import unittest
import uuid

BACKEND = Path(__file__).resolve().parents[1]
MIGRATIONS = BACKEND / 'hub/src/main/resources/db/migration'
SOURCE = BACKEND / 'commons/src/main/java/com/iflytek/astron/console/commons/tenant/TenantAccessRepository.java'
MIGRATION = (MIGRATIONS / 'V1.49__add_platform_tenant_boundaries.sql').read_text(encoding='utf-8')
AUTH_QUERY = re.search(r'jdbc.query\("""(.*?)"""', SOURCE.read_text(encoding='utf-8'), re.S).group(1)


def command(*args, input=None, check=True):
    return subprocess.run(args, input=input, text=True, capture_output=True, check=check, timeout=120)


def table_from_upstream(filename, table):
    source = (MIGRATIONS / filename).read_text(encoding='utf-8')
    return re.search(r'CREATE TABLE `' + re.escape(table) + r'`\s*\(.*?\) ENGINE=.*?;', source, re.S).group(0)


class TenantMigrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.container = 'open-agent-tenant-verification-' + uuid.uuid4().hex[:12]
        image = os.environ.get('MYSQL_TEST_IMAGE', 'mysql:8.4')
        command('docker', 'image', 'inspect', image)
        command('docker', 'run', '--detach', '--name', cls.container, '--label', 'open-agent.verification=tenant',
                '--network', 'none', '--memory', '768m', '--cpus', '1', '--pids-limit', '256',
                '-e', 'MYSQL_ALLOW_EMPTY_PASSWORD=yes', image)
        cls.addClassCleanup(cls.cleanup_container)
        deadline = time.monotonic() + 100
        while time.monotonic() < deadline:
            # The image starts a temporary socket-only server during initialization.
            # TCP becomes available only after the real server starts; socket polling races shutdown.
            ready = command('docker', 'exec', cls.container, 'mysql', '--protocol=TCP', '-h127.0.0.1', '-uroot', '--batch', '-e', 'SELECT 1', check=False)
            if ready.returncode == 0:
                break
            time.sleep(1)
        else:
            raise RuntimeError('isolated verification MySQL did not become ready')
        version = command('docker', 'exec', cls.container, 'mysql', '--protocol=TCP', '-h127.0.0.1', '-uroot', '-N', '-e', 'SELECT VERSION()').stdout.strip()
        print('MySQL verification version:', version, flush=True)

    @classmethod
    def cleanup_container(cls):
        label = command('docker', 'inspect', '--format', '{{index .Config.Labels "open-agent.verification"}}', cls.container).stdout.strip()
        if label != 'tenant' or not cls.container.startswith('open-agent-tenant-verification-'):
            raise RuntimeError('refusing to remove an unrelated container')
        command('docker', 'rm', '-f', cls.container)

    def setUp(self):
        self.database = 'oa_verify_' + uuid.uuid4().hex[:12]
        self.sql('CREATE DATABASE ' + self.database + ' CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci', database=False)
        self.sql(table_from_upstream('V1.1__init_core.sql', 'user_info'))
        self.sql(table_from_upstream('V1.3__init_space.sql', 'agent_space'))
        self.sql(table_from_upstream('V1.3__init_space.sql', 'agent_space_user'))

    def sql(self, sql, database=True, check=True):
        args = ['docker', 'exec', '-i', self.container, 'mysql', '--protocol=TCP', '-h127.0.0.1', '-uroot', '--batch', '--skip-column-names', '--default-character-set=utf8mb4']
        if database:
            args.append(self.database)
        result = command(*args, input=sql, check=False)
        if check and result.returncode != 0:
            self.fail(result.stderr)
        return result.stdout.strip() if check else result

    def seed(self):
        self.sql("""
            INSERT INTO user_info(uid, username, account_status, deleted) VALUES
            ('alice', 'Alice', 1, 0), ('bob', 'Bob', 1, 0), ('carol', 'Carol', 2, 0), ('deleted', 'Deleted', 1, 1);
            INSERT INTO agent_space(id,name,uid,deleted) VALUES
            (10,'Tenant A','alice',0),(20,'Tenant B','bob',0),(30,'Archived space','deleted',1);
            INSERT INTO agent_space_user(space_id,uid,role) VALUES
            (10,'alice',1),(10,'bob',3),(10,'carol',2),(10,'orphan',1),(20,'bob',1),(30,'deleted',1);
        """)

    def authorize(self, tenant, environment, uid):
        # Inputs in this test are constants, escaped here to exercise the exact production SELECT.
        query = AUTH_QUERY
        for value in (tenant, environment, uid):
            query = query.replace('?', "'" + value.replace("'", "''") + "'", 1)
        return self.sql(query)

    def test_empty_database(self):
        self.sql(MIGRATION)
        self.assertEqual('0', self.sql('SELECT COUNT(*) FROM oa_tenant'))
        self.assertEqual('0', self.sql('SELECT COUNT(*) FROM oa_platform_admin'))
        self.assertEqual('5', self.sql("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=DATABASE() AND table_name LIKE 'oa_%'"))

    def test_existing_data_maps_once_without_granting_production(self):
        self.seed()
        before = self.sql('SELECT space_id,uid,role FROM agent_space_user ORDER BY id')
        self.sql(MIGRATION)
        self.assertEqual(before, self.sql('SELECT space_id,uid,role FROM agent_space_user ORDER BY id'))
        self.assertEqual('5', self.sql('SELECT COUNT(*) FROM oa_tenant'))
        self.assertEqual('10', self.sql('SELECT COUNT(*) FROM oa_tenant_environment'))
        self.assertEqual('7', self.sql('SELECT COUNT(*) FROM oa_tenant_member'))
        self.assertEqual('0', self.sql("SELECT COUNT(*) FROM oa_tenant_member WHERE environment='prod'"))
        self.assertEqual('0', self.sql('SELECT COUNT(*) FROM oa_platform_admin'))
        self.assertEqual('5', self.sql('SELECT COUNT(*) FROM oa_tenant_audit WHERE retain_until > created_at'))
        self.assertEqual('tenant_admin', self.sql("SELECT role FROM oa_tenant_member WHERE tenant_id='space-10' AND uid='alice'"))
        self.assertEqual('developer', self.sql("SELECT role FROM oa_tenant_member WHERE tenant_id='space-10' AND uid='bob'"))
        self.assertEqual('0', self.sql("SELECT COUNT(*) FROM oa_tenant WHERE space_id=30 OR personal_uid='deleted'"))

    def test_real_authorization_query_rejects_wrong_scope_and_frozen_users(self):
        self.seed()
        self.sql(MIGRATION)
        self.assertIn('tenant_admin', self.authorize('space-10', 'test', 'alice'))
        self.assertEqual('', self.authorize('space-20', 'test', 'alice'))
        self.assertEqual('', self.authorize('space-10', 'prod', 'alice'))
        self.assertEqual('', self.authorize('space-10', 'test', 'ALICE'))
        self.assertEqual('', self.authorize('space-10', 'test', 'carol'))
        self.assertEqual('', self.authorize('space-10', 'test', 'orphan'))
        self.sql("DELETE FROM agent_space_user WHERE space_id=10 AND uid='alice'")
        self.assertEqual('', self.authorize('space-10', 'test', 'alice'))
        self.sql('UPDATE agent_space SET deleted=1 WHERE id=20')
        self.assertEqual('', self.authorize('space-20', 'test', 'bob'))

    def test_personal_resource_mapping_cannot_be_impersonated(self):
        self.seed()
        self.sql(MIGRATION)
        alice_tenant = self.sql("SELECT tenant_id FROM oa_tenant WHERE personal_uid='alice'")
        self.assertIn('tenant_admin', self.authorize(alice_tenant, 'test', 'alice'))
        self.sql("INSERT INTO oa_tenant_member(tenant_id,environment,uid,role) VALUES ('" + alice_tenant + "','test','bob','tenant_admin')")
        self.assertEqual('', self.authorize(alice_tenant, 'test', 'bob'))

    def test_database_constraints_reject_invalid_scope_role_and_quota(self):
        self.seed()
        self.sql(MIGRATION)
        for statement in (
            "UPDATE oa_tenant_environment SET max_concurrent_runs=-1 WHERE tenant_id='space-10'",
            "UPDATE oa_tenant_environment SET environment='staging' WHERE tenant_id='space-10' AND environment='test'",
            "UPDATE oa_tenant_member SET role='platform_admin' WHERE uid='alice'",
            "UPDATE oa_tenant SET status='unknown' WHERE tenant_id='space-10'",
            "INSERT INTO oa_tenant(tenant_id,display_name,scope_kind,space_id) VALUES ('duplicate','No','space',10)",
            "INSERT INTO oa_tenant(tenant_id,display_name,scope_kind) VALUES ('unscoped','No','personal')",
        ):
            self.assertNotEqual(0, self.sql(statement, check=False).returncode, statement)

    def test_scoped_model_migration_and_version_constraints(self):
        self.seed()
        self.sql(MIGRATION)
        self.sql((MIGRATIONS / 'V1.50__add_scoped_model_connections.sql').read_text(encoding='utf-8'))
        self.assertEqual('0', self.sql('SELECT COUNT(*) FROM oa_model_connection'))
        self.sql("INSERT INTO oa_model_connection(tenant_id,environment,connection_id,display_name,current_version,created_by) VALUES ('space-10','test','model-a','Internal model',1,'alice')")
        version = """INSERT INTO oa_model_connection_version(tenant_id,environment,connection_id,version,endpoint,model_name,
            supports_stream,supports_tools,supports_usage,connect_timeout_seconds,read_timeout_seconds,total_timeout_seconds,
            credential_ref,credential_key_id,credential_nonce,credential_cipher) VALUES
            ('space-10','test','model-a',1,'https://llm.internal/v1','model',true,true,true,10,90,180,
            'credential-a','v1',UNHEX('000000000000000000000000'),UNHEX('aabbccdd'))"""
        self.sql(version)
        self.assertNotEqual(0, self.sql(version, check=False).returncode)
        self.assertEqual('0', self.sql("SELECT COUNT(*) FROM oa_model_connection_version WHERE tenant_id='space-20' AND environment='test' AND connection_id='model-a'"))
        self.assertEqual('0', self.sql("SELECT COUNT(*) FROM oa_model_connection_version WHERE tenant_id='space-10' AND environment='prod' AND connection_id='model-a'"))
        for mutation in (
            "UPDATE oa_model_connection_version SET total_timeout_seconds=5",
            "UPDATE oa_model_connection_version SET connect_timeout_seconds=0",
            "UPDATE oa_model_connection_version SET protocol='unknown'",
            "UPDATE oa_model_connection_version SET environment='prod'",
            "UPDATE oa_model_connection_version SET tenant_id='space-20'",
        ):
            self.assertNotEqual(0, self.sql(mutation, check=False).returncode)
        self.assertEqual('1', self.sql("SELECT COUNT(*) FROM oa_model_connection_version WHERE revoked=false"))
        self.sql("UPDATE oa_model_connection_version SET revoked=true WHERE credential_ref='credential-a'")
        self.assertEqual('0', self.sql("SELECT COUNT(*) FROM oa_model_connection_version WHERE revoked=false"))


if __name__ == '__main__':
    unittest.main(verbosity=2)
