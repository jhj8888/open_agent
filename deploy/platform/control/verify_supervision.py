"""Verify installed supervisor routes and migration using protected host credentials."""

from datetime import datetime, timezone
import json
import urllib.error
import urllib.request

from provision_data import ROOT, container_address, private_file, installation_lock


def main():
    keys = json.loads((ROOT / 'api-secrets.json').read_text())
    endpoint = 'http://' + container_address('open-agent-control-api') + ':18082'
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(path, identity, secret, body):
        message = urllib.request.Request(endpoint + path, data=json.dumps(body).encode(), method='POST',
            headers={'Content-Type': 'application/json', 'X-Platform-Key-Id': identity, 'X-Platform-Service-Key': secret})
        try:
            with opener.open(message, timeout=10) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            return error.code, None

    path = '/internal/platform/reconcile/pending'
    status, rows = request(path, 'demo-supervisor', keys['reconciler_key'], {'limit': 1})
    if status != 200 or not isinstance(rows, list):
        raise RuntimeError('Installed supervision table/route is unavailable')
    for identity, key in [('demo-worker', 'worker_key'), ('demo-dispatcher', 'dispatcher_key')]:
        if request(path, identity, keys[key], {'limit': 1})[0] != 403:
            raise RuntimeError('Worker or dispatcher can enter supervisor boundary')
    if request('/internal/platform/worker/runs/00000000-0000-0000-0000-000000000000/runtime',
               'demo-supervisor', keys['reconciler_key'], {})[0] != 403:
        raise RuntimeError('Supervisor credential can read worker runtime secrets')
    if request('/internal/platform/reconcile/attempts/00000000-0000-0000-0000-000000000000/inspect',
               'demo-supervisor', keys['reconciler_key'], {})[0] != 404:
        raise RuntimeError('Unknown attempt boundary is inconsistent')
    report = {'recorded_at': datetime.now(timezone.utc).isoformat(),
              'artifact_sha256': json.loads((ROOT / 'api-deployment.json').read_text())['artifact_sha256'],
              'supervision_table_and_private_api_available': True, 'worker_dispatcher_denied': True,
              'reconciler_cannot_read_model_credentials': True, 'unknown_attempt_rejected': True,
              'admission_enabled': False, 'public_execution_available': False}
    private_file(ROOT / 'supervision-verification.json', json.dumps(report, indent=2) + '\n', replace=True)
    print(json.dumps(report), flush=True)


if __name__ == '__main__':
    with installation_lock():
        main()
