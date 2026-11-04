"""Prove reply-port safety and continued rejection of non-loopback new traffic."""

from datetime import datetime, timezone
import json
from pathlib import Path
import socket
import time
from uuid import uuid4

from provision_data import ROOT, NETWORK, command, container_address, private_file


def counter():
    rows = command('iptables', '-L', 'INPUT', '-n', '-v', '-x').stdout.splitlines()
    return sum(int(row.split()[0]) for row in rows if 'open-agent-sandbox-published-deny' in row)


def main():
    if (Path('/opt/open-agent/sandbox/.owner').exists()
            and Path('/opt/open-agent/sandbox/.owner').is_symlink()):
        raise RuntimeError('Unexpected sandbox owner marker')
    destination = container_address('open-agent-control-identity')
    probes = []
    for port in [45941, 46141, 46441]:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
            connection.settimeout(2)
            connection.bind(('0.0.0.0', port))
            started = time.monotonic()
            connection.connect((destination, 8000))
            probes.append({'source_port': port, 'connected': True, 'seconds': round(time.monotonic() - started, 3)})
    for binary in ['iptables', 'ip6tables']:
        rule = ['!', '-i', 'lo', '-p', 'tcp', '--dport', '46000:46499']
        comment = ['-m', 'comment', '--comment', 'open-agent-sandbox-published-deny', '-j', 'DROP']
        command(binary, '-w', '5', '-C', 'INPUT', *rule, '-m', 'conntrack', '!', '--ctstate', 'ESTABLISHED,RELATED', *comment)
        if not command(binary, '-w', '5', '-C', 'INPUT', *rule, *comment, check=False).returncode:
            raise RuntimeError('Legacy reply-blocking rule still present')
    network = json.loads(command('docker', 'network', 'inspect', NETWORK).stdout)[0]
    if network.get('Labels', {}).get('io.open-agent.owner') != 'open_agent.control.demo.v1':
        raise RuntimeError('Unowned verification network')
    gateway = network['IPAM']['Config'][0]['Gateway']
    sandbox = json.loads(Path('/opt/open-agent/sandbox/deployment.json').read_text())
    name = 'oa-verify-ingress-' + uuid4().hex[:12]
    before = counter()
    # The temporary listener carries no data, and is protected by the new INPUT
    # rule. A separate owned bridge tests a NEW connection, not a host reply.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind((gateway, 46497))
        listener.listen(1)
        probe = ('import socket,sys\n'
                 's=socket.socket();s.settimeout(2)\n'
                 'try:s.connect((' + repr(gateway) + ',46497))\n'
                 'except TimeoutError:print("new_connection_denied");sys.exit(0)\n'
                 'except OSError:sys.exit(2)\n'
                 'sys.exit(1)\n')
        try:
            result = command('docker', 'run', '--rm', '--pull=never', '--name', name,
                '--label', 'io.open-agent.verification=reply-guard', '--network', NETWORK,
                '--read-only', '--cap-drop=ALL', '--security-opt', 'no-new-privileges',
                '--user', '1000:1000', '--memory', '128m', '--pids-limit', '32',
                '--entrypoint', 'python3', sandbox['runtime_image'], '-c', probe, timeout=12)
            if result.stdout.strip() != 'new_connection_denied' or counter() <= before:
                raise RuntimeError('New ingress denial was not observed at the guard')
        finally:
            # Exact random verification name only; never remove shared containers.
            command('docker', 'rm', '--force', name, check=False)
    report = {'checked_at': datetime.now(timezone.utc).isoformat(), 'reply_probes': probes,
              'new_non_loopback_connection_denied': True, 'guard_drop_counter_increased': True,
              'ipv4_ipv6_rules_verified': True, 'legacy_rules_absent': True,
              'shared_firewall_reloaded': False, 'probe_container_removed': True}
    private_file(ROOT / 'network-reply-guard-verification.json', json.dumps(report, indent=2) + '\n', replace=True)
    print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()
