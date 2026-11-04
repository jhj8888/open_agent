"""Real S3 authorization/persistence check; uses the worker's locked official Boto3 SDK.

Runs as the authorized host administrator. Only restarts this installer's labelled
S3 container. Leaves the application database empty for initial Flyway migration.
"""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
from uuid import uuid4

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError, EndpointConnectionError
from provision_data import DATABASE, installation_lock

ROOT = Path('/opt/open-agent/control')
OWNER = 'open_agent.control.demo.v1'
NETWORK = 'open-agent-control-data'
BUCKET = 'open-agent-skills'


def inspect(name):
    result = json.loads(subprocess.check_output(['docker', 'inspect', name]))[0]
    if result['Config'].get('Labels', {}).get('io.open-agent.owner') != OWNER:
        raise RuntimeError('Unrelated container')
    return result


def client(prefix, values):
    address = inspect('open-agent-control-s3')['NetworkSettings']['Networks'][NETWORK]['IPAddress']
    return boto3.client('s3', endpoint_url='http://' + address + ':8333', region_name='us-east-1',
                        aws_access_key_id=values[prefix + '_key'], aws_secret_access_key=values[prefix + '_secret'],
                        config=Config(signature_version='s3v4', connect_timeout=3, read_timeout=10,
                                      retries={'max_attempts': 1}, proxies={}, s3={'addressing_style': 'path'}))


def denied(operation):
    try:
        operation()
    except ClientError as error:
        if error.response['ResponseMetadata']['HTTPStatusCode'] == 403:
            return
        raise RuntimeError('Unexpected denial response') from None
    raise RuntimeError('Operation was incorrectly authorized')


def verify_database_accounts(values):
    inspect('open-agent-control-mysql')
    for user, key, allowed, forbidden in (
        ('open_agent', 'platform_db', 'SELECT key_id FROM ' + DATABASE + '.oa_deployment_credential LIMIT 0',
         'SELECT 1 FROM open_agent_identity.user LIMIT 0'),
        ('open_agent_identity', 'identity_db', 'SELECT 1 FROM open_agent_identity.user LIMIT 0',
         'SELECT key_id FROM ' + DATABASE + '.oa_deployment_credential LIMIT 0'),
    ):
        for query, should_succeed in ((allowed, True), (forbidden, False)):
            result = subprocess.run(
                ['docker', 'exec', '-i', '-e', 'MYSQL_PWD', 'open-agent-control-mysql', 'mysql',
                 '--protocol=TCP', '-h127.0.0.1', '-u' + user, '-N'],
                input=query + ';\n', capture_output=True, text=True, timeout=15,
                env={**os.environ, 'MYSQL_PWD': values[key]})
            if should_succeed and result.returncode != 0:
                raise RuntimeError('Database service account cannot read its own schema')
            if not should_succeed and (result.returncode == 0 or '1142' not in result.stderr):
                raise RuntimeError('Cross-schema access was not denied by database privileges')


def main():
    if (ROOT / '.owner').read_text() != OWNER:
        raise RuntimeError('Installer ownership mismatch')
    values = json.loads((ROOT / 'secrets.json').read_text())
    verify_database_accounts(values)
    admin, app = client('s3_admin', values), client('s3_app', values)
    try:
        admin.head_bucket(Bucket=BUCKET)
    except ClientError as error:
        if error.response['ResponseMetadata']['HTTPStatusCode'] != 404:
            raise RuntimeError('Unexpected bucket lookup response') from None
        admin.create_bucket(Bucket=BUCKET)
    denied(lambda: app.create_bucket(Bucket='open-agent-forbidden-' + uuid4().hex))
    other_bucket = 'open-agent-verification-' + uuid4().hex
    admin.create_bucket(Bucket=other_bucket)
    try:
        denied(lambda: app.head_bucket(Bucket=other_bucket))
        # SeaweedFS filters ListBuckets to the caller's authorized buckets.
        if any(item['Name'] != BUCKET for item in app.list_buckets().get('Buckets', [])):
            raise RuntimeError('S3 exposed an unauthorized bucket name')
    finally:
        admin.delete_bucket(Bucket=other_bucket)
    key = 'verification/' + uuid4().hex + '/persistent.bin'
    payload = os.urandom(1024 * 1024)
    try:
        app.put_object(Bucket=BUCKET, Key=key, Body=payload, ContentType='application/octet-stream')
        with app.get_object(Bucket=BUCKET, Key=key)['Body'] as content:
            if content.read() != payload:
                raise RuntimeError('S3 roundtrip mismatch')
        denied(lambda: app.put_object_acl(Bucket=BUCKET, Key=key, ACL='public-read'))
        inspect('open-agent-control-s3')
        subprocess.run(['docker', 'restart', '-t', '30', 'open-agent-control-s3'],
                       check=True, stdout=subprocess.DEVNULL, timeout=60)
        app = client('s3_app', values)
        for _ in range(90):
            try:
                with app.get_object(Bucket=BUCKET, Key=key)['Body'] as content:
                    actual = content.read()
                if actual != payload:
                    raise RuntimeError('S3 data changed after restart')
                break
            except (EndpointConnectionError, ClientError):
                time.sleep(2)
        else:
            raise RuntimeError('S3 persistence check did not recover')
        listed = app.list_objects_v2(Bucket=BUCKET, Prefix=key)
        if not any(item['Key'] == key for item in listed.get('Contents', [])):
            raise RuntimeError('S3 list did not return persisted object')
    finally:
        app.delete_object(Bucket=BUCKET, Key=key)
    result = {'s3_roundtrip_bytes': len(payload), 'sha256': hashlib.sha256(payload).hexdigest(),
              'cross_bucket_create_denied': True, 'cross_bucket_read_denied': True,
              'bucket_list_filtered': True, 'public_acl_denied': True,
              's3_restart_persistence': True, 'probe_removed': True,
              'database_accounts_cross_schema_denied': True,
              'boto3_version': boto3.__version__, 'production_ha_verified': False}
    path = ROOT / 'data-verification.json'
    path.write_text(json.dumps(result, indent=2) + '\n')
    path.chmod(0o600)
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    with installation_lock():
        main()
