"""Project-owned HTTPS listener, separate from the server's existing port 80 apps.

Uses upstream Caddy 2.11.8 with ACME TLS-ALPN and the shortlived IP certificate
profile. No HTTP listener, admin API, or internal execution routes are exposed.
"""

import hashlib
import json
import os
from pathlib import Path
import pwd
import socket
import subprocess
import tarfile

ROOT = Path('/opt/open-agent/ingress')
UNIT = Path('/etc/systemd/system/open-agent-ingress.service')
OWNER = 'open_agent.ingress.demo.v1'
VERSION = '2.11.8'
SHA256 = 'f032f8805e4df2ce5e1a8a624495c91e9ff763d8ff3ceddadd4784a07c472f40'
ADDRESS = '120.26.81.83'


def command(*args, check=True, timeout=30):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if check and result.returncode:
        raise RuntimeError('Ingress command failed: ' + args[0])
    return result


def write(path, data, mode=0o644):
    if path.is_symlink():
        raise RuntimeError('Linked ingress file')
    path.write_bytes(data.encode() if isinstance(data, str) else data)
    path.chmod(mode)


def main():
    if os.geteuid() != 0 or ROOT.is_symlink():
        raise RuntimeError('Ingress installation requires owned root directory')
    if ROOT.exists() and (not (ROOT / '.owner').exists() or (ROOT / '.owner').read_text().strip() != OWNER):
        raise RuntimeError('Refusing unowned ingress directory')
    if UNIT.exists() and '# ' + OWNER not in UNIT.read_text():
        raise RuntimeError('Refusing unowned ingress service')
    if command('systemctl', 'is-active', '--quiet', 'open-agent-ingress', check=False).returncode:
        with socket.socket() as probe:
            probe.bind(('0.0.0.0', 443))
    archive = Path(__file__).parent / ('caddy_' + VERSION + '_linux_amd64.tar.gz')
    if hashlib.sha256(archive.read_bytes()).hexdigest() != SHA256:
        raise RuntimeError('Caddy archive checksum mismatch')
    ROOT.mkdir(mode=0o755, exist_ok=True)
    write(ROOT / '.owner', OWNER + '\n', 0o600)
    binary = ROOT / ('caddy-' + VERSION)
    with tarfile.open(archive, 'r:gz') as package:
        entry = package.getmember('caddy')
        if not entry.isfile() or entry.size > 150 * 1024 * 1024:
            raise RuntimeError('Invalid Caddy binary')
        content = package.extractfile(entry).read()
        if binary.exists() and binary.read_bytes() != content:
            raise RuntimeError('Immutable Caddy binary differs')
        if not binary.exists():
            write(binary, content, 0o755)
    if not command(str(binary), 'version').stdout.startswith('v' + VERSION + ' '):
        raise RuntimeError('Caddy version mismatch')
    try:
        user = pwd.getpwnam('open-agent-ingress')
    except KeyError:
        command('useradd', '--system', '--no-create-home', '--shell', '/sbin/nologin', 'open-agent-ingress')
        user = pwd.getpwnam('open-agent-ingress')
    if user.pw_uid == 0 or user.pw_shell not in {'/sbin/nologin', '/usr/sbin/nologin', '/bin/false'}:
        raise RuntimeError('Unsafe ingress user')
    state = ROOT / 'state'
    state.mkdir(mode=0o700, exist_ok=True)
    os.chown(state, user.pw_uid, user.pw_gid)
    state.chmod(0o700)
    config = f'''{{
    admin off
    persist_config off
    auto_https disable_redirects
    skip_install_trust
    default_sni {ADDRESS}
    servers {{
        protocols h1 h2
    }}
}}
https://{ADDRESS} {{
    tls {{
        issuer acme {{
            profile shortlived
            disable_http_challenge
        }}
    }}
    header {{
        X-Content-Type-Options nosniff
        Referrer-Policy no-referrer
        X-Frame-Options SAMEORIGIN
        -Server
    }}
    handle /healthz {{
        respond "open-agent-ingress-ready" 200
    }}
    handle /internal/* {{
        respond 404
    }}
    handle {{
        respond "Open Agent console is being prepared." 503
    }}
}}
'''
    # Later console rollout owns a separately reviewed route file. Keep it on rerun.
    routes = ROOT / 'console-routes.caddy'
    if routes.exists():
        config = config.replace('    handle {\n        respond "Open Agent console is being prepared." 503\n    }',
                                '    import ' + str(routes))
    write(ROOT / 'Caddyfile', config)
    command(str(binary), 'validate', '--config', str(ROOT / 'Caddyfile'), '--adapter', 'caddyfile')
    unit = f'''# {OWNER}
[Unit]
Description=Open Agent dedicated HTTPS ingress
After=network-online.target
Wants=network-online.target

[Service]
User=open-agent-ingress
Group=open-agent-ingress
WorkingDirectory={ROOT}
Environment=XDG_DATA_HOME={state}/data
Environment=XDG_CONFIG_HOME={state}/config
ExecStart={binary} run --config {ROOT}/Caddyfile --adapter caddyfile
Restart=on-failure
RestartSec=5
TimeoutStopSec=30
AmbientCapabilities=CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
NoNewPrivileges=yes
PrivateTmp=yes
PrivateDevices=yes
ProtectSystem=strict
ProtectHome=yes
ProtectKernelTunables=yes
ProtectControlGroups=yes
ReadWritePaths={state}
UMask=0077
MemoryMax=256M
TasksMax=128

[Install]
WantedBy=multi-user.target
'''
    write(UNIT, unit)
    # Add only HTTPS; never reload the shared firewall or alter existing services.
    # Clear the short-lived connectivity probe's timeout once, without a
    # global reload. Subsequent installs preserve the ordinary runtime rule.
    firewall_marker = ROOT / '.firewall-persistent'
    if not firewall_marker.exists():
        command('firewall-cmd', '--remove-service=https', check=False)
        command('firewall-cmd', '--add-service=https')
        write(firewall_marker, OWNER + '\n', 0o600)
    for flags in ([], ['--permanent']):
        if command('firewall-cmd', *flags, '--query-service=https', check=False).returncode:
            command('firewall-cmd', *flags, '--add-service=https')
    command('systemctl', 'daemon-reload')
    command('systemctl', 'enable', '--now', 'open-agent-ingress')
    command('systemctl', 'restart', 'open-agent-ingress')
    result = {'version': VERSION, 'archive_sha256': SHA256, 'https_origin': 'https://' + ADDRESS,
              'http_listener': False, 'admin_api': False, 'certificate_validation_pending': True}
    write(ROOT / 'deployment.json', json.dumps(result, indent=2) + '\n')
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
