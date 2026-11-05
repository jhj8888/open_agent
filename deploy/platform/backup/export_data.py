"""Export a small encrypted data/config repository from an existing cold snapshot.

Software images and runtimes belong to the separately pinned delivery bundle.
The original full snapshot is retained; no forget/prune operation is performed.
"""
import json
import os
from pathlib import Path
import tarfile

import recovery as r

INCLUDE = [
    '/opt/open-agent/control/mysql', '/opt/open-agent/control/seaweed',
    '/opt/open-agent/control/identity', '/opt/open-agent/control/*.json',
    '/opt/open-agent/control/*.env', '/opt/open-agent/control/*.conf',
    '/opt/open-agent/control/mysql-root', '/opt/open-agent/control/mysql-init.sql', '/opt/open-agent/supervisor',
    '/opt/open-agent/agent-worker/state', '/opt/open-agent/agent-worker/attempts',
    '/opt/open-agent/temporal/state', '/opt/open-agent/sandbox/state',
    '/opt/open-agent/sandbox/config.toml', '/opt/open-agent/sandbox/api-key',
    '/opt/open-agent/sandbox/deployment.json', '/var/lib/open-agent-mlflow',
    '/opt/open-agent/observability/*.json', '/opt/open-agent/observability/*.ini',
    '/opt/open-agent/observability/*.env', '/opt/open-agent/ingress/state',
    '/opt/open-agent/ingress/Caddyfile', '/opt/open-agent/ingress/*.caddy',
    '/opt/open-agent/monitor/*.yml', '/opt/open-agent/monitor/observer.key',
    '/opt/open-agent/recovery', '/etc/systemd/system',
]


def export():
    r.installation()
    source = json.loads((r.HOME / 'backup-report.json').read_text())['snapshot_id']
    if len(source) != 64 or any(c not in '0123456789abcdef' for c in source):
        raise RuntimeError('invalid_source_snapshot')
    previous_report = r.HOME / 'data-export-report.json'
    if previous_report.exists():
        previous = json.loads(previous_report.read_text())
        if previous['source_full_snapshot'] == source and previous['includes'] == INCLUDE:
            archive = Path(previous['archive'])
            if archive.parent != r.HOME / 'export' or archive.is_symlink() or r.digest(archive) != previous['sha256']:
                raise RuntimeError('previous_data_export_changed')
            r.restic('--repo', str(r.HOME / 'data-repository'), 'check', '--read-data')
            r.event('encrypted_data_export_already_verified', bytes=previous['bytes'], sha256=previous['sha256'])
            return
    args = ['rewrite', '--json']
    for path in INCLUDE:
        args += ['--include', path]
    args += [source]
    checkpoint = r.HOME / 'data-export-state.json'
    state = json.loads(checkpoint.read_text()) if checkpoint.exists() else {}
    if state.get('source') == source and state.get('includes') == INCLUDE:
        matches = [s for s in json.loads(r.restic('snapshots', '--json')) if s['id'] == state['projection']]
    else:
        before = {item['id'] for item in json.loads(r.restic('snapshots', '--json'))}
        r.restic(*args)
        snapshots = json.loads(r.restic('snapshots', '--json'))
        matches = [item for item in snapshots if item['id'] not in before]
    if len(matches) != 1:
        raise RuntimeError('expected_one_new_data_projection')
    projection = matches[0]['id']
    r.save(checkpoint, {'source': source, 'includes': INCLUDE, 'projection': projection})
    destination = r.HOME / 'data-repository'
    if destination.is_symlink():
        raise RuntimeError('linked_data_repository')
    if not destination.exists():
        r.restic('--repo', str(destination), 'init')
    r.restic('--repo', str(destination), 'copy', '--from-repo', str(r.REPO),
             '--from-password-file', str(r.PASSWORD), projection)
    r.restic('--repo', str(destination), 'check', '--read-data')
    copied = json.loads(r.restic('--repo', str(destination), 'snapshots', '--json'))
    # The copy command may change the snapshot ID; original records its source.
    selected = [s for s in copied if s['tree'] == matches[0]['tree']
                and s.get('original') in (projection, source) and s['time'] == matches[0]['time']]
    if len(selected) != 1:
        raise RuntimeError('copied_snapshot_not_found')
    copied_id = selected[0]['id']
    check_dir = r.HOME / ('data-verify-' + copied_id[:12])
    if check_dir.exists():
        raise RuntimeError('data_verify_target_exists')
    check_dir.mkdir(mode=0o700)
    r.restic('--repo', str(destination), 'restore', copied_id, '--target', str(check_dir), '--verify')
    required = ['opt/open-agent/control/mysql/auto.cnf', 'opt/open-agent/control/s3.json',
                'opt/open-agent/control/api-secrets.json', 'opt/open-agent/control/secrets.json',
                'opt/open-agent/control/identity-secrets.json', 'opt/open-agent/recovery/manifest.json',
                'opt/open-agent/temporal/state/temporal.db', 'var/lib/open-agent-mlflow/tracking.db']
    for path in required:
        if not (check_dir / path).is_file():
            raise RuntimeError('required_recovery_data_missing')
    for path in ['opt/open-agent/control/seaweed', 'opt/open-agent/control/mysql/astron_console',
                 'opt/open-agent/control/mysql/open_agent_identity']:
        if not (check_dir / path).is_dir() or not any((check_dir / path).iterdir()):
            raise RuntimeError('required_recovery_directory_empty')
    exported = r.HOME / 'export'
    exported.mkdir(mode=0o700, exist_ok=True)
    r.owned_directory(exported)
    archive = exported / ('data-' + copied_id + '.tar')
    if archive.exists() or archive.is_symlink():
        raise RuntimeError('data_export_target_exists')
    with tarfile.open(archive, 'w') as output:
        output.add(destination, arcname='repository', recursive=True)
    result = {'source_full_snapshot': source, 'source_data_snapshot': projection,
              'exported_data_snapshot': copied_id, 'includes': INCLUDE,
              'archive': str(archive), 'bytes': archive.stat().st_size, 'sha256': r.digest(archive),
              'all_encrypted_pack_data_verified': True, 'data_restored_and_verified': True,
              'key_included': False, 'original_full_snapshot_retained': True,
              'software_bundle_required_separately': True, 'created_at': r.utc()}
    r.save(r.HOME / 'data-export-report.json', result)
    r.event('encrypted_data_repository_exported', bytes=result['bytes'], sha256=result['sha256'])


if __name__ == '__main__':
    os.umask(0o077)
    try:
        with r.lock():
            export()
    except Exception as error:
        r.event('data_export_failed', error_type=type(error).__name__,
                error_code=str(error) if isinstance(error, RuntimeError) else 'unexpected_error')
        raise SystemExit(1) from None
