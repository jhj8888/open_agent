"""Verify the built images against a fresh, private restore of actual platform data.

This proves image/container behavior, not Kubernetes/CNI/Kata cluster behavior.
Uses the previously deployed backup helpers and persistent cleanup journal.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import sys
import time
import urllib.error
import urllib.request
from uuid import uuid4

sys.path.insert(0, '/opt/open-agent/recovery')
import recovery as r
import verify_restore as drill
from demo_session import DemoSession
from verify_task_outputs import raw

BUILD = Path('/opt/open-agent/image-build')
JOURNAL = BUILD / 'smoke-maintenance.json'
OWNER = 'open_agent.platform.images.v1'


def cleanup():
    state = json.loads(JOURNAL.read_text())
    if state.get('owner') != OWNER or not re.fullmatch(r'[0-9a-f]{12}', state['id']):
        raise RuntimeError('image_smoke_journal_invalid')
    backup_state = json.loads(drill.DRILL_JOURNAL.read_text())
    if backup_state['id'] != state['id']:
        raise RuntimeError('image_smoke_cleanup_scope_changed')
    web_error = False
    for name in ['oa-image-web-' + state['id'], 'oa-image-tls-' + state['id']]:
        result = r.run('docker', 'inspect', name, check=False)
        if not result.returncode:
            item = json.loads(result.stdout)[0]
            if item['Config'].get('Labels', {}).get(r.LABEL) != OWNER:
                web_error = True
            elif r.run('docker', 'rm', '-f', name, check=False).returncode:
                web_error = True
    # Always attempt to restore the original observation services, even if an
    # unexpected web container prevents complete network cleanup.
    drill.cleanup()
    if web_error:
        raise RuntimeError('image_smoke_web_cleanup_incomplete')
    state.update(phase='cleaned', cleaned_at=r.utc())
    r.save(JOURNAL, state)


def api_container(image_id, original, target, network, identifier):
    env = dict(entry.split('=', 1) for entry in original['Config']['Env'])
    env.update(PLATFORM_RUN_ADMISSION_ENABLED='false', PLATFORM_SERVICE_KEYS='{}',
               PLATFORM_RECONCILER_WORKERS='{}', MODEL_ALLOWED_ORIGINS='[]', MODEL_PUBLIC_DEMO_ORIGINS='[]',
               PLATFORM_PROMETHEUS_URL='http://127.0.0.1:9',
               PLATFORM_OIDC_JWKS='http://open-agent-control-identity:8000/.well-known/jwks')
    path = target / 'api.env'
    r.private(path, ''.join(k + '=' + v + '\n' for k, v in env.items()))
    name = 'oa-restore-' + identifier + '-api'
    r.run('docker', 'create', '--name', name, '--network', network,
          '--network-alias', 'open-agent-control-api', '--network-alias', 'fixture-platform-api',
          '--label', r.LABEL + '=' + drill.DRILL_OWNER, '--read-only', '--cap-drop', 'ALL',
          '--security-opt', 'no-new-privileges:true', '--memory', '896m', '--memory-swap', '896m',
          '--cpus', '1', '--pids-limit', '256', '--tmpfs', '/tmp:rw,nosuid,noexec,size=128m,uid=65531,gid=65531',
          '--log-driver', 'json-file', '--log-opt', 'max-size=2m', '--log-opt', 'max-file=1',
          '--env-file', str(path), image_id)
    return name


def verify(config, tls_config):
    r.installation()
    r.assert_idle()
    if JOURNAL.exists() and json.loads(JOURNAL.read_text()).get('phase') != 'cleaned':
        raise RuntimeError('cleanup_prior_image_smoke_first')
    if drill.DRILL_JOURNAL.exists() and json.loads(drill.DRILL_JOURNAL.read_text())['phase'] != 'cleaned':
        raise RuntimeError('cleanup_prior_restore_first')
    build = json.loads((BUILD / 'build-report.json').read_text())
    if r.digest(Path(build['archive']['path'])) != build['archive']['sha256']:
        raise RuntimeError('image_archive_checksum_mismatch')
    r.run('docker', 'image', 'load', '--input', build['archive']['path'], timeout=600)
    for value in build['images'].values():
        image = json.loads(r.run('docker', 'image', 'inspect', value['local_image_id']).stdout)[0]
        if image['Config'].get('Labels', {}).get(r.LABEL) != OWNER or image['Config']['User'] != '65531:65531':
            raise RuntimeError('image_provenance_or_user_mismatch')
    identifier = uuid4().hex[:12]
    target = BUILD / ('smoke-' + identifier)
    target.mkdir(mode=0o700)
    network = 'oa-restore-' + identifier
    paused = [u for u in ['open-agent-trace-exporter.service', 'open-agent-mlflow.service'] if r.unit_active(u)]
    state = {'owner': drill.DRILL_OWNER, 'id': identifier, 'phase': 'image_smoke', 'paused': paused,
             'containers': ['oa-restore-' + identifier + '-' + c for c in ('mysql', 's3', 'identity', 'api')],
             'network': network, 'started_at': r.utc()}
    r.save(drill.DRILL_JOURNAL, state)
    r.save(JOURNAL, {'owner': OWNER, 'id': identifier, 'phase': 'active'})
    started = time.monotonic()
    try:
        data = json.loads((r.HOME / 'data-export-report.json').read_text())
        r.restic('--repo', str(r.HOME / 'data-repository'), 'restore', data['exported_data_snapshot'],
                 '--target', str(target), '--verify')
        for unit in paused:
            r.run('systemctl', 'stop', unit)
        available = next(int(l.split()[1]) for l in Path('/proc/meminfo').read_text().splitlines() if l.startswith('MemAvailable:'))
        if available < 1800 * 1024:
            raise RuntimeError('image_smoke_insufficient_memory')
        r.run('docker', 'network', 'create', '--internal', '--label', r.LABEL + '=' + drill.DRILL_OWNER, network)
        found = json.loads(r.run('docker', 'network', 'inspect', network).stdout)[0]
        if not found['Internal'] or found['EnableIPv6']:
            raise RuntimeError('image_smoke_network_not_isolated')
        state['bridge'] = 'br-' + found['Id'][:12]
        r.save(drill.DRILL_JOURNAL, state)
        drill.network_guard(state, True)
        manifest = json.loads((target / 'opt/open-agent/recovery/manifest.json').read_text())
        names = []
        for component in ['mysql', 's3', 'identity']:
            name = drill.clone(manifest['containers']['open-agent-control-' + component], target, network, identifier)
            names.append(name)
            r.run('docker', 'start', name)
            if component == 'mysql':
                for _ in range(60):
                    try:
                        if r.sql('SELECT 1;', name) == '1': break
                    except RuntimeError:
                        pass
                    time.sleep(2)
                else: raise RuntimeError('image_smoke_mysql_unavailable')
                for database, expected in manifest['database_fingerprints'].items():
                    if r.database_fingerprint(database, name) != expected:
                        raise RuntimeError('image_smoke_data_differ')
            if component == 'identity':
                r.wait_http('http://' + drill.address(name) + ':8000/.well-known/jwks')
        api = api_container(build['images']['api']['local_image_id'], manifest['containers']['open-agent-control-api'],
                            target, network, identifier)
        r.run('docker', 'start', api)
        r.wait_http('http://' + drill.address(api) + ':18082/health/ready')
        r.event('private_api_image_ready')
        web_config = target / 'Caddyfile'
        web_config.write_bytes(config.read_bytes())
        web_config.chmod(0o644)
        tls = target / 'Caddyfile.https'
        tls.write_bytes(tls_config.read_bytes())
        tls.chmod(0o644)
        # Public CA certificates only, used to validate the HTTPS configuration.
        ca = Path('/etc/pki/tls/certs/ca-bundle.crt')
        if not ca.is_file(): raise RuntimeError('system_public_ca_bundle_missing')
        r.run('docker', 'run', '--rm', '--name', 'oa-image-tls-' + identifier,
              '--label', r.LABEL + '=' + OWNER, '--network', 'none', '--read-only', '--cap-drop', 'ALL',
              '--security-opt', 'no-new-privileges:true', '--memory', '64m', '--memory-swap', '64m',
              '--mount', f'type=bind,src={tls},dst=/etc/caddy/Caddyfile,readonly',
              '--mount', f'type=bind,src={ca},dst=/etc/open-agent-ca/ca.pem,readonly',
              '--entrypoint', '/usr/bin/caddy', build['images']['web']['local_image_id'],
              'validate', '--config', '/etc/caddy/Caddyfile', '--adapter', 'caddyfile')
        web = 'oa-image-web-' + identifier
        r.run('docker', 'create', '--name', web, '--network', network, '--label', r.LABEL + '=' + OWNER,
              '--read-only', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true',
              '--memory', '64m', '--memory-swap', '64m', '--cpus', '0.5', '--pids-limit', '64',
              '--tmpfs', '/tmp:rw,nosuid,noexec,size=32m,uid=65531,gid=65531',
              '--mount', f'type=bind,src={web_config},dst=/etc/caddy/Caddyfile,readonly',
              build['images']['web']['local_image_id'])
        r.run('docker', 'start', web)
        endpoint = 'http://' + drill.address(web) + ':8080'
        r.wait_http(endpoint + '/health/live')
        arguments = dict(api=endpoint, identity_endpoint=endpoint + '/identity',
                         state_root=target / 'opt/open-agent/control')
        session = DemoSession(**arguments)
        other = DemoSession('member-demo', json.loads((arguments['state_root'] / 'member-secrets.json').read_text())['password'],
                            require_tenant=False, **arguments)
        static, headers = raw(session, '/open-agent/')
        if hashlib.sha256(static).hexdigest() != build['inputs']['workbench_files']['index.html']:
            raise RuntimeError('image_smoke_static_mismatch')
        if 'Content-Security-Policy' not in headers: raise RuntimeError('image_smoke_csp_missing')
        for path, expected in build['inputs']['workbench_files'].items():
            body, _ = raw(session, '/open-agent/' + path)
            if hashlib.sha256(body).hexdigest() != expected: raise RuntimeError('image_smoke_asset_mismatch')
        for route in ['/internal/platform/dispatch/pending', '/internal/platform/metrics', '/health/ready', '/actuator/env']:
            raw(session, route, 404)
        forged = type('Forged', (), {'api': endpoint, 'opener': session.opener,
                  'auth': {'X-User-Id': 'admin', 'X-Tenant-Id': 'space-1', 'X-Platform-Role': 'reconciler'}})()
        raw(forged, '/platform/tenants', 401)
        files = 0
        runs = set()
        for row in r.sql("SELECT run_id,output_id,sha256,size_bytes FROM oa_run_output WHERE state='available' AND tenant_id='space-1';", names[0]).splitlines():
            run_id, output_id, expected, size = row.split('\t')
            route = f'/api/v1/runs/{run_id}/outputs/{output_id}/download'
            body, _ = raw(session, route)
            if hashlib.sha256(body).hexdigest() != expected or len(body) != int(size):
                raise RuntimeError('image_smoke_output_mismatch')
            raw(other, route, 404)
            files += 1
            runs.add(run_id)
        steps = 0
        for run_id in sorted(runs):
            after = 0
            while True:
                page = session.call(f'/api/v1/runs/{run_id}/observations?after={after}&limit=200')
                steps += len(page['items'])
                if not page['hasMore']: break
                after = page['next']
            raw(other, f'/api/v1/runs/{run_id}/observations', 404)
        existing = r.sql('SELECT count(*) FROM oa_agent_run;', names[0])
        deployment = session.call(session.management + '/agent-deployments')[0]['deployment_id']
        request = urllib.request.Request(endpoint + '/api/v1/agent-deployments/' + deployment + '/runs',
            data=json.dumps({'input': {'message': 'Admission check; must not execute.', 'history': []}, 'timeoutSeconds': 30}).encode(),
            headers={**session.auth, 'Content-Type': 'application/json', 'Idempotency-Key': str(uuid4())})
        try:
            session.opener.open(request, timeout=15).close()
        except urllib.error.HTTPError as error:
            if error.code != 503: raise RuntimeError('image_smoke_admission_status_invalid') from None
        else: raise RuntimeError('image_smoke_admission_unexpectedly_enabled')
        if r.sql('SELECT count(*) FROM oa_agent_run;', names[0]) != existing:
            raise RuntimeError('image_smoke_created_business_task')
        result = {'verified_at': r.utc(), 'images': build['images'], 'archive_sha256': build['archive']['sha256'],
                  'elapsed_seconds': round(time.monotonic() - started, 3), 'database_fingerprints_equal': True,
                  'fresh_data_restore': True, 'read_only_nonroot_containers': True, 'published_host_ports': False,
                  'internal_network': True, 'tls_upstream_config_validated': True, 'real_oidc_login_via_web_proxy': True,
                  'static_assets_sha256_verified': len(build['inputs']['workbench_files']), 'outputs_downloaded_and_verified': files,
                  'steps_read': steps, 'cross_tenant_denied': True, 'internal_routes_hidden': True, 'forged_identity_rejected': True,
                  'admission_rejected_without_task_creation': True, 'docker_load_verified_cached_daemon': True,
                  'kubernetes_runtime_verified': False}
        r.save(BUILD / 'smoke-report.json', result)
        r.event('private_platform_images_verified', files=files, steps=steps, seconds=result['elapsed_seconds'])
        return result
    finally:
        cleanup()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path, default=Path(__file__).parent / 'Caddyfile.smoke')
    parser.add_argument('--tls-config', type=Path, default=Path(__file__).parent / 'Caddyfile.https-smoke')
    parser.add_argument('--cleanup', action='store_true')
    args = parser.parse_args()
    os.umask(0o077)
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(InterruptedError('image_smoke_interrupted')))
    try:
        with r.lock():
            cleanup() if args.cleanup else verify(args.config, args.tls_config)
    except Exception as error:
        r.event('private_image_smoke_failed', error_type=type(error).__name__,
                error_code=str(error) if isinstance(error, RuntimeError) else 'unexpected_error')
        raise SystemExit(1) from None
