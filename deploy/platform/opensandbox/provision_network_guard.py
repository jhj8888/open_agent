"""Persist project-scoped IPv4/IPv6 guards without reloading shared firewalld.

Existing Docker/host services are unchanged. Only the dedicated sandbox bridge
and reserved sandbox port range are denied to non-loopback clients.
"""

import json
import os
from pathlib import Path
import subprocess
import sys

BRIDGE = 'oa-sandbox0'
MARKER = '# open_agent.sandbox.network_guard.v1'


def command(*args, check=True):
    result = subprocess.run(args, capture_output=True, text=True, timeout=20)
    if check and result.returncode:
        raise RuntimeError('Sandbox network guard command failed: ' + args[0])
    return result


def rules():
    for direction in ('-i', '-o'):
        yield 'FORWARD', [direction, BRIDGE, '-m', 'comment', '--comment', 'open-agent-sandbox-forward-deny', '-j', 'DROP']
    yield 'INPUT', ['-i', BRIDGE, '-m', 'conntrack', '!', '--ctstate', 'ESTABLISHED,RELATED', '-m', 'comment',
                    '--comment', 'open-agent-sandbox-host-deny', '-j', 'DROP']
    yield 'INPUT', ['!', '-i', 'lo', '-p', 'tcp', '--dport', '46000:46499', '-m', 'comment', '--comment',
                    'open-agent-sandbox-published-deny', '-j', 'DROP']


def main(install=True):
    if os.geteuid() != 0:
        raise RuntimeError('Sandbox network guard requires administrator')
    network = json.loads(command('docker', 'network', 'inspect', 'open-agent-sandbox').stdout)[0]
    if network.get('Labels', {}).get('io.open-agent.component') != 'opensandbox-demo' or network['Options'].get('com.docker.network.bridge.name') != BRIDGE:
        raise RuntimeError('Unowned sandbox network')
    command('firewall-cmd', '--state')
    for family, executable in (('ipv4', 'iptables'), ('ipv6', 'ip6tables')):
        for chain, rule in rules():
            for flags in (['--permanent'], []):
                query = command('firewall-cmd', *flags, '--direct', '--query-rule', family, 'filter', chain, '0', *rule, check=False)
                if query.returncode:
                    command('firewall-cmd', *flags, '--direct', '--add-rule', family, 'filter', chain, '0', *rule)
            # Early rules complement permanent direct chains in the existing
            # Docker/firewalld setup. The admission verifier checks these live.
            if command(executable, '-w', '5', '-C', chain, *rule, check=False).returncode:
                command(executable, '-w', '5', '-I', chain, '1', *rule)
    if install:
        target = Path('/opt/open-agent/sandbox/network_guard.py')
        unit = Path('/etc/systemd/system/open-agent-network-guard.service')
        if target.is_symlink() or unit.is_symlink() or (unit.exists() and MARKER not in unit.read_text()):
            raise RuntimeError('Unowned network guard installation')
        if target.exists() and not unit.exists():
            raise RuntimeError('Unowned network guard source')
        target.write_bytes(Path(__file__).read_bytes())
        target.chmod(0o600)
        unit.write_text(f'''{MARKER}
[Unit]
Description=Open Agent sandbox network isolation guard
After=docker.service firewalld.service
Requires=docker.service firewalld.service
Before=open-agent-supervisor.service

[Service]
Type=oneshot
ExecStart=/usr/bin/python3 {target} --apply-only
RemainAfterExit=yes
TimeoutStartSec=120

[Install]
WantedBy=multi-user.target
''')
        unit.chmod(0o644)
        command('systemctl', 'daemon-reload')
        command('systemctl', 'enable', '--now', 'open-agent-network-guard.service')
    print(json.dumps({'ipv4': True, 'ipv6': True, 'permanent_firewalld_rules': True,
                      'shared_firewall_reloaded': False, 'bridge': BRIDGE}), flush=True)


if __name__ == '__main__':
    main(install='--apply-only' not in sys.argv)
