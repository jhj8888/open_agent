"""Install this project's static interface preview without touching other apps."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import pwd
import re
import socket
import subprocess
import tarfile
import time
from urllib.request import urlopen

ROOT = Path('/opt/open-agent/preview')
UNIT = Path('/etc/systemd/system/open-agent-preview.service')
MARKER = '# Managed by open_agent: interface preview only'
PORT = 18080


def command(*args, check=True):
    return subprocess.run(args, check=check, capture_output=True, text=True, timeout=45)


def main(archive, digest, static_web_root=None):
    if os.geteuid() != 0 or not re.fullmatch('[a-f0-9]{64}', digest):
        raise ValueError('Requires authorized host administrator and SHA-256')
    if hashlib.sha256(archive.read_bytes()).hexdigest() != digest:
        raise ValueError('Archive checksum mismatch')
    if UNIT.exists() and MARKER not in UNIT.read_text():
        raise ValueError('Refusing to change an unrelated service')
    active = command('systemctl', 'is-active', 'open-agent-preview', check=False).returncode == 0
    if not active:
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', PORT))
    root = ROOT / 'releases' / digest
    root.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, 'r:gz') as package:
        members = package.getmembers()
        names = [m.name for m in members]
        if len(names) > 200 or len(set(names)) != len(names) or sum(m.size for m in members) > 20 * 1024 * 1024:
            raise ValueError('Invalid static archive size or duplicate names')
        if 'public/index.html' not in names or 'serve.py' not in names:
            raise ValueError('Missing preview files')
        for member in members:
            path = PurePosixPath(member.name)
            if not member.isfile() or path.is_absolute() or any(p in ('', '.', '..') or p.startswith('.') for p in path.parts):
                raise ValueError('Only ordinary relative files are allowed')
            if member.name != 'serve.py' and not member.name.startswith('public/'):
                raise ValueError('Unexpected archive member')
            content = package.extractfile(member).read()
            destination = root.joinpath(*path.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists() and destination.read_bytes() != content:
                raise ValueError('Existing immutable release differs')
            destination.write_bytes(content)
            destination.chmod(0o644)
    try:
        account = pwd.getpwnam('open-agent-preview')
    except KeyError:
        command('useradd', '--system', '--no-create-home', '--shell', '/sbin/nologin', 'open-agent-preview')
        account = pwd.getpwnam('open-agent-preview')
    if account.pw_uid == 0 or account.pw_shell not in ('/sbin/nologin', '/usr/sbin/nologin', '/bin/false'):
        raise ValueError('Unexpected preview account')
    unit = f'''{MARKER}
[Unit]
Description=Open Agent static interface preview (sample data only)
After=network.target
[Service]
Type=simple
User=open-agent-preview
Group=open-agent-preview
WorkingDirectory={root}
ExecStart=/usr/bin/python3 {root}/serve.py --root {root}/public --host 127.0.0.1 --port {PORT}
Restart=on-failure
RestartSec=3
NoNewPrivileges=yes
PrivateTmp=yes
PrivateDevices=yes
ProtectSystem=strict
ProtectHome=yes
ProtectKernelTunables=yes
ProtectControlGroups=yes
RestrictSUIDSGID=yes
RestrictAddressFamilies=AF_INET AF_UNIX
CapabilityBoundingSet=
UMask=0077
MemoryMax=192M
TasksMax=32
[Install]
WantedBy=multi-user.target
'''
    previous = UNIT.read_text() if UNIT.exists() else None
    UNIT.write_text(unit)
    command('systemctl', 'daemon-reload')
    try:
        command('systemctl', 'enable', '--now', 'open-agent-preview')
        if active:
            command('systemctl', 'restart', 'open-agent-preview')
        for attempt in range(20):
            try:
                with urlopen(f'http://127.0.0.1:{PORT}/healthz', timeout=2) as response:
                    health = json.load(response)
                if health['mode'] == 'interface-preview':
                    break
            except OSError:
                time.sleep(0.5)
        else:
            raise RuntimeError('Preview health check failed')
    except BaseException:
        if previous:
            UNIT.write_text(previous)
            command('systemctl', 'daemon-reload')
            command('systemctl', 'restart', 'open-agent-preview')
        else:
            command('systemctl', 'disable', '--now', 'open-agent-preview', check=False)
        raise
    record = {'mode': 'interface-preview', 'archive_sha256': digest, 'port': PORT,
              'release': str(root), 'service': 'open-agent-preview', 'model_connected': False,
              'health': health}
    if static_web_root is not None:
        # Optional existing static hosting: add only a project-owned link. Do
        # not edit or restart the existing web application or its configuration.
        web_root = static_web_root.resolve(strict=True)
        if not web_root.is_dir():
            raise ValueError('Static web root must already exist')
        link = web_root / 'open-agent-preview'
        if link.exists() or link.is_symlink():
            if not link.is_symlink() or ROOT / 'releases' not in link.resolve().parents:
                raise ValueError('Refusing to replace an unrelated web path')
        staging = web_root / '.open-agent-preview.next'
        if staging.exists() or staging.is_symlink():
            raise ValueError('Unexpected staging link exists')
        staging.symlink_to(root / 'public', target_is_directory=True)
        staging.replace(link)
        with urlopen('http://127.0.0.1/open-agent-preview/', timeout=5) as response:
            if response.read() != (root / 'public/index.html').read_bytes():
                raise RuntimeError('Existing static host did not serve the exact approved index')
        record['public_path'] = '/open-agent-preview/'
    (ROOT / 'deployment.json').write_text(json.dumps(record, indent=2) + '\n')
    print(json.dumps(record))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--sha256', required=True)
    parser.add_argument('--static-web-root', type=Path)
    args = parser.parse_args()
    main(args.archive, args.sha256, args.static_web_root)
