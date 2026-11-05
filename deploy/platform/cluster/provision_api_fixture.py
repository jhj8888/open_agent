"""Test-only, agentless K3s API. No kubelet, runtime, CNI or tenant execution."""
from pathlib import Path
from hashlib import sha256
import json
import os
import ipaddress
import subprocess
import time
import yaml

ROOT = Path('/opt/open-agent/k3s-api-fixture')
OWNER = 'open-agent-k3s-api-fixture-v1'
NAME = 'open-agent-k3s-api-contract'
BASE = 'm.daocloud.io/docker.io/library/eclipse-temurin@sha256:1eac8b8fee11ca7d6725a48bc5d41992ed1561504ebae25490d2d062847e8269'
SOURCE = Path(__file__).resolve().parent

def run(args, timeout=60, required=True):
    result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    if required and result.returncode:
        (ROOT / 'last-error.log').write_bytes(result.stderr)
        raise RuntimeError('k3s_fixture_command_failed')
    return result

def inspect(kind, name):
    result = run(['docker', kind, 'inspect', name], required=False)
    return json.loads(result.stdout)[0] if result.returncode == 0 else None

def main():
    os.umask(0o077)
    if ROOT.is_symlink() or ROOT.parent.resolve() != Path('/opt/open-agent'):
        raise RuntimeError('fixture_path_invalid')
    if ROOT.exists():
        if (ROOT / 'owner').read_text() != OWNER: raise RuntimeError('fixture_not_owned')
    else:
        ROOT.mkdir(mode=0o700)
        (ROOT / 'owner').write_text(OWNER)
    metadata = json.loads((SOURCE / 'k3s-fixture-artifact.json').read_text())
    binary = SOURCE / 'k3s'
    if sha256(binary.read_bytes()).hexdigest() != metadata['sha256']:
        raise RuntimeError('fixture_binary_digest_mismatch')
    binary.chmod(0o500)
    previous = inspect('container', NAME)
    if previous is not None:
        record = json.loads((ROOT / 'installation.json').read_text())
        if (record['owner'] != OWNER or record['container_id'] != previous['Id']
                or record['binary'] != metadata or record['phase'] != 'starting'):
            raise RuntimeError('fixture_container_already_exists_inspect_before_retry')
        return finish(previous['Id'], metadata, time.monotonic())
    available = next(int(line.split()[1]) for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemAvailable:'))
    if available < 1400000: raise RuntimeError('fixture_memory_headroom_insufficient')
    for kind in ['network', 'volume']:
        value = inspect(kind, NAME)
        if value is not None and value.get('Labels', {}).get('open-agent.owner') != OWNER:
            raise RuntimeError('fixture_resource_not_owned')
        if value is None:
            args = ['docker', kind, 'create', '--label', 'open-agent.owner=' + OWNER]
            if kind == 'network': args += ['--internal']
            run(args + [NAME])
    command = ['docker', 'run', '-d', '--name', NAME, '--label', 'open-agent.owner=' + OWNER,
        '--network', NAME, '--memory', '768m', '--memory-swap', '768m',
        '--cpus', '1', '--pids-limit', '256', '--read-only', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
        '--tmpfs', '/tmp:rw,nosuid,nodev,size=256m', '--tmpfs', '/run:rw,nosuid,nodev,size=32m',
        '--tmpfs', '/etc/rancher:rw,nosuid,nodev,size=8m,mode=700',
        '--mount', 'type=volume,source=' + NAME + ',target=/var/lib/rancher/k3s',
        '--mount', 'type=bind,source=' + str(binary) + ',target=/fixture/k3s,readonly',
        '--entrypoint', '/fixture/k3s', BASE, 'server', '--disable-agent',
        '--disable=coredns,servicelb,traefik,local-storage,metrics-server', '--disable-helm-controller',
        '--disable-cloud-controller', '--disable-network-policy', '--flannel-backend=none',
        '--egress-selector-mode=disabled', '--bind-address=0.0.0.0', '--https-listen-port=6443',
        '--write-kubeconfig=/etc/rancher/k3s/k3s.yaml', '--write-kubeconfig-mode=600',
        '--kube-apiserver-arg=anonymous-auth=false', '--kube-apiserver-arg=profiling=false',
        '--kube-controller-manager-arg=profiling=false', '--kube-scheduler-arg=profiling=false']
    started = time.monotonic()
    identifier = run(command).stdout.decode().strip()
    (ROOT / 'installation.json').write_text(json.dumps({'owner': OWNER, 'container_id': identifier, 'network': NAME, 'volume': NAME,
                                                      'binary': metadata, 'base': BASE, 'phase': 'starting'}, indent=2))
    finish(identifier, metadata, started)


def finish(identifier, metadata, started):
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        state = inspect('container', NAME)
        if (state is None or state['Id'] != identifier
                or state['Config']['Labels'].get('open-agent.owner') != OWNER
                or state['HostConfig']['Privileged'] or state['HostConfig']['CapDrop'] != ['ALL']):
            raise RuntimeError('fixture_container_not_owned_or_isolated')
        if not state['State']['Running']:
            (ROOT / 'server.log').write_bytes(run(['docker', 'logs', NAME], required=False).stdout)
            raise RuntimeError('fixture_container_stopped')
        try:
            result = run(['docker', 'exec', identifier, '/fixture/k3s', 'kubectl', '--kubeconfig=/etc/rancher/k3s/k3s.yaml',
                          '--request-timeout=2s', 'get', '--raw=/readyz'], timeout=8, required=False)
            if result.returncode == 0 and result.stdout.strip() == b'ok': break
        except subprocess.TimeoutExpired:
            pass  # A cold CLI process may outlast its own HTTP timeout; recheck the same fixture only.
        time.sleep(2)
    else:
        (ROOT / 'server.log').write_bytes(run(['docker', 'logs', NAME], required=False).stdout)
        raise RuntimeError('fixture_not_ready')
    state = inspect('container', NAME)
    network = inspect('network', NAME)
    if not network['Internal'] or state['HostConfig']['PortBindings']:
        raise RuntimeError('fixture_network_not_private')
    address = state['NetworkSettings']['Networks'][NAME]['IPAddress']
    if not ipaddress.ip_address(address).is_private:
        raise RuntimeError('fixture_address_not_private')
    endpoint = address + ':6443'
    # Docker cp cannot read this tmpfs; capture bytes privately, never print credentials.
    configuration = yaml.safe_load(run(['docker', 'exec', identifier, 'cat', '/etc/rancher/k3s/k3s.yaml']).stdout)
    configuration['clusters'][0]['cluster'].update(server='https://' + endpoint, **{'tls-server-name': 'localhost'})
    (ROOT / 'admin.yaml').write_text(yaml.safe_dump(configuration))
    (ROOT / 'admin.yaml').chmod(0o600)
    report = {'version': metadata['version'], 'container_id': identifier, 'private_endpoint': 'https://' + endpoint,
              'startup_seconds': round(time.monotonic() - started, 2), 'memory_limit_mib': 768,
              'agentless': True, 'privileged': False, 'host_network': False, 'public_port': False, 'capabilities_dropped': 'ALL', 'phase': 'ready'}
    (ROOT / 'ready.json').write_text(json.dumps(report, indent=2))
    record = json.loads((ROOT / 'installation.json').read_text())
    record['phase'] = 'ready'
    (ROOT / 'installation.json').write_text(json.dumps(record, indent=2))
    print(json.dumps(report))

if __name__ == '__main__':
    main()
