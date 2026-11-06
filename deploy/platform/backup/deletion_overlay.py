"""Preserve trusted deletion evidence when restoring an older, isolated snapshot.

The caller must keep the target API/workers stopped during merge. SQL is sent to
the official mysql batch client without --force or reconnect. No object is
deleted here: the existing exact-key reconciler owns physical cleanup.
"""
from datetime import datetime
import hashlib
import json
import re

FIELDS = ('run_id', 'tenant_id', 'environment', 'deployment_id', 'idempotency_key',
          'request_sha256', 'snapshot_sha256', 'run_actor_id', 'deleted_by', 'deleted_at')


def validate(rows):
    if not isinstance(rows, list) or len(rows) > 50000:
        raise RuntimeError('deletion_overlay_size_invalid')
    seen = set()
    requests = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != set(FIELDS) or any(not isinstance(v, str) for v in row.values()):
            raise RuntimeError('deletion_overlay_record_invalid')
        for field in ('run_id', 'deployment_id'):
            if not re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', row[field]):
                raise RuntimeError('deletion_overlay_identity_invalid')
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', row['tenant_id']) or row['environment'] not in ('test', 'prod'):
            raise RuntimeError('deletion_overlay_scope_invalid')
        if not re.fullmatch(r'[!-~]{1,128}', row['idempotency_key']):
            raise RuntimeError('deletion_overlay_key_invalid')
        for field in ('request_sha256', 'snapshot_sha256'):
            if not re.fullmatch(r'[0-9a-f]{64}', row[field]):
                raise RuntimeError('deletion_overlay_digest_invalid')
        for field in ('run_actor_id', 'deleted_by'):
            if not row[field].strip() or len(row[field]) > 128 or '\0' in row[field]:
                raise RuntimeError('deletion_overlay_actor_invalid')
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}', row['deleted_at']):
            raise RuntimeError('deletion_overlay_time_invalid')
        datetime.strptime(row['deleted_at'], '%Y-%m-%d %H:%M:%S.%f')
        request = tuple(row[k] for k in ('tenant_id', 'environment', 'deployment_id', 'idempotency_key'))
        if row['run_id'] in seen or request in requests:
            raise RuntimeError('deletion_overlay_duplicate')
        seen.add(row['run_id'])
        requests.add(request)
    return rows


def export(sql):
    # A single SELECT sees both original deletions and previously restored fences.
    # Hex avoids mysql batch escaping of JSON, Unicode and actor/key punctuation.
    columns = ','.join(FIELDS)
    query = """
        SET SESSION time_zone='+00:00';
        SELECT HEX(JSON_OBJECT(
          'run_id',run_id,'tenant_id',tenant_id,'environment',environment,
          'deployment_id',deployment_id,'idempotency_key',idempotency_key,
          'request_sha256',request_sha256,'snapshot_sha256',snapshot_sha256,
          'run_actor_id',run_actor_id,'deleted_by',deleted_by,
          'deleted_at',DATE_FORMAT(deleted_at,'%Y-%m-%d %H:%i:%s.%f')))
        FROM (
          SELECT d.run_id,d.tenant_id,d.environment,r.deployment_id,r.idempotency_key,
                 r.request_sha256,r.snapshot_sha256,r.actor_id run_actor_id,
                 d.actor_id deleted_by,d.deleted_at
          FROM oa_run_deletion d JOIN oa_agent_run r ON r.run_id=d.run_id
          UNION
          SELECT """ + columns + " FROM oa_run_deletion_fence) records ORDER BY run_id LIMIT 50001;"
    rows = []
    for line in sql(query).splitlines():
        row = json.loads(bytes.fromhex(line).decode('utf-8'))
        row['deleted_at'] = row['deleted_at'][:-3]  # MySQL emits six, schema stores three.
        rows.append(row)
    return validate(rows)


def merge_sql(rows):
    validate(rows)
    statements = ["SET SESSION time_zone='+00:00'",
                  "SET SESSION sql_mode='STRICT_ALL_TABLES,NO_ZERO_DATE,NO_ZERO_IN_DATE'",
                  "CREATE TEMPORARY TABLE oa_deleted_restore_stage LIKE oa_run_deletion_fence",
                  "CREATE TEMPORARY TABLE oa_deleted_restore_guard(valid int NOT NULL CHECK(valid=1))"]
    for row in rows:
        # Hex string literals prevent SQL/client command injection in identifiers or actor names.
        values = ','.join("CONVERT(X'" + row[k].encode('utf-8').hex() + "' USING utf8mb4)" for k in FIELDS)
        statements.append('INSERT INTO oa_deleted_restore_stage(' + ','.join(FIELDS) + ') VALUES(' + values + ')')
    statements.append('START TRANSACTION')

    def require_empty(query):
        statements.append('INSERT INTO oa_deleted_restore_guard(valid) SELECT 0 WHERE EXISTS(' + query + ')')

    same_request = ' AND '.join('r.' + k + '=s.' + k for k in ('tenant_id', 'environment', 'deployment_id', 'idempotency_key'))
    require_empty('SELECT 1 FROM oa_deleted_restore_stage s JOIN oa_agent_run r ON ' + same_request + ' WHERE r.run_id<>s.run_id')
    comparison = ' AND '.join('r.' + k + '=s.' + k for k in ('tenant_id', 'environment', 'deployment_id', 'idempotency_key', 'request_sha256', 'snapshot_sha256'))
    require_empty("SELECT 1 FROM oa_deleted_restore_stage s JOIN oa_agent_run r ON r.run_id=s.run_id WHERE NOT ("
                  + comparison + " AND r.actor_id=s.run_actor_id) OR r.state NOT IN ('succeeded','failed','timed_out','cancelled')")
    for table, condition in [('oa_run_attempt', "r.lease_state<>'released'"), ('oa_run_sandbox', "r.phase<>'released'")]:
        require_empty('SELECT 1 FROM oa_deleted_restore_stage s JOIN ' + table + ' r ON r.run_id=s.run_id WHERE ' + condition)
    match_fence = ' AND '.join('f.' + k + '=s.' + k for k in FIELDS)
    require_empty('SELECT 1 FROM oa_deleted_restore_stage s JOIN oa_run_deletion_fence f ON f.run_id=s.run_id OR ('
                  + ' AND '.join('f.' + k + '=s.' + k for k in ('tenant_id', 'environment', 'deployment_id', 'idempotency_key'))
                  + ') WHERE NOT (' + match_fence + ')')
    require_empty('SELECT 1 FROM oa_deleted_restore_stage s JOIN oa_run_deletion d ON d.run_id=s.run_id '
                  'WHERE d.tenant_id<>s.tenant_id OR d.environment<>s.environment OR d.actor_id<>s.deleted_by OR d.deleted_at<>s.deleted_at')
    require_empty('SELECT 1 FROM oa_run_output o JOIN oa_deleted_restore_stage s ON s.run_id=o.run_id '
                  'WHERE o.object_key IS NOT NULL AND NOT EXISTS(SELECT 1 FROM oa_run_output_upload u '
                  'WHERE u.output_id=o.output_id AND u.object_key=o.object_key)')
    statements += [
        'INSERT INTO oa_run_deletion_fence(' + ','.join(FIELDS) + ') SELECT '
        + ','.join('s.' + k for k in FIELDS) + ' FROM oa_deleted_restore_stage s '
        'WHERE NOT EXISTS(SELECT 1 FROM oa_run_deletion_fence f WHERE f.run_id=s.run_id)',
        "INSERT INTO oa_tenant_audit(tenant_id,environment,actor_uid,action,subject_id,after_value,retain_until) "
        "SELECT s.tenant_id,s.environment,'system:restore','run.deletion_restored',s.run_id,"
        "JSON_OBJECT('deleted_by',s.deleted_by,'deleted_at',s.deleted_at),"
        "TIMESTAMPADD(DAY,e.audit_retention_days,CURRENT_TIMESTAMP(3)) "
        "FROM oa_deleted_restore_stage s JOIN oa_agent_run r ON r.run_id=s.run_id "
        "JOIN oa_tenant_environment e ON e.tenant_id=s.tenant_id AND e.environment=s.environment "
        "WHERE NOT EXISTS(SELECT 1 FROM oa_run_deletion d WHERE d.run_id=s.run_id)",
        'INSERT INTO oa_run_deletion(run_id,tenant_id,environment,actor_id,deleted_at) '
        'SELECT s.run_id,s.tenant_id,s.environment,s.deleted_by,s.deleted_at FROM oa_deleted_restore_stage s '
        'JOIN oa_agent_run r ON r.run_id=s.run_id WHERE NOT EXISTS(SELECT 1 FROM oa_run_deletion d WHERE d.run_id=s.run_id)',
        'UPDATE oa_run_deletion d JOIN oa_deleted_restore_stage s ON d.run_id=s.run_id SET d.purged_at=NULL',
        "UPDATE oa_run_output_upload u JOIN oa_run_output o ON o.output_id=u.output_id "
        "JOIN oa_deleted_restore_stage s ON s.run_id=o.run_id SET u.state='abandoned',u.reconcile_after=CURRENT_TIMESTAMP(3)",
        "UPDATE oa_run_output o JOIN oa_deleted_restore_stage s ON s.run_id=o.run_id SET o.state='failed',o.object_key=NULL",
        'COMMIT',
        'SELECT COUNT(*),COALESCE(SUM(r.run_id IS NOT NULL),0) FROM oa_deleted_restore_stage s LEFT JOIN oa_agent_run r ON r.run_id=s.run_id'
    ]
    return ';\n'.join(statements) + ';\n'


def merge(sql, rows):
    result = sql(merge_sql(rows)).strip().split('\t')
    if len(result) != 2 or any(not value.isdigit() for value in result) or int(result[0]) != len(rows):
        raise RuntimeError('deletion_overlay_result_invalid')
    return {'records': int(result[0]), 'restored_tasks_hidden': int(result[1]),
            'absent_tasks_fenced': int(result[0]) - int(result[1]),
            'records_sha256': hashlib.sha256(json.dumps(rows, sort_keys=True, ensure_ascii=False).encode()).hexdigest()}
