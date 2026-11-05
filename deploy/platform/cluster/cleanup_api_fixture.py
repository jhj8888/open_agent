"""Remove only the recorded disposable API fixture, never a production cluster."""
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess

ROOT = Path('/opt/open-agent/k3s-api-fixture')
OWNER = 'open-agent-k3s-api-fixture-v1'
NAME = 'open-agent-k3s-api-contract'


def docker(*args):
    return subprocess.check_output(['docker', *args], stderr=subprocess.PIPE, timeout=45)


def main():
    if ROOT.is_symlink() or ROOT.resolve() != Path('/opt/open-agent/k3s-api-fixture'):
        raise RuntimeError('fixture_path_invalid')
    if (ROOT / 'owner').read_text() != OWNER:
        raise RuntimeError('fixture_not_owned')
    record = json.loads((ROOT / 'installation.json').read_text())
    identifier = record['container_id']
    if record['owner'] != OWNER or not re.fullmatch('[0-9a-f]{64}', identifier):
        raise RuntimeError('fixture_record_invalid')
    if record['network'] != NAME or record['volume'] != NAME:
        raise RuntimeError('fixture_record_invalid')
    container = json.loads(docker('container', 'inspect', identifier))[0]
    network = json.loads(docker('network', 'inspect', NAME))[0]
    volume = json.loads(docker('volume', 'inspect', NAME))[0]
    for resource in (network, volume):
        if resource.get('Labels', {}).get('open-agent.owner') != OWNER:
            raise RuntimeError('fixture_resource_not_owned')
    if (container['Id'] != identifier or container['Name'] != '/' + NAME
            or container['Config']['Labels'].get('open-agent.owner') != OWNER
            or not network['Internal'] or set(network['Containers']) - {identifier}):
        raise RuntimeError('fixture_container_or_network_not_owned')
    docker('stop', '--time', '10', identifier)
    docker('rm', identifier)
    docker('volume', 'rm', NAME)
    docker('network', 'rm', NAME)
    # Admin credentials are only for the deleted private fixture; retain nonsecret evidence.
    config = ROOT / 'admin.yaml'
    if config.is_symlink():
        raise RuntimeError('fixture_config_symlink')
    config.unlink(missing_ok=True)
    result = {'container_removed': True, 'volume_removed': True, 'network_removed': True,
              'admin_credentials_removed': True, 'checked_at': datetime.now(timezone.utc).isoformat()}
    (ROOT / 'cleanup.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
