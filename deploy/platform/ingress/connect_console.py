"""Expose only user-facing platform and identity APIs on the owned HTTPS ingress."""

import ipaddress
import json
from pathlib import Path
import subprocess
import urllib.error
import urllib.request

ROOT = Path('/opt/open-agent/ingress')
MARKER = '# open_agent.ingress.console.v1'


def command(*args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise RuntimeError('Console ingress command failed: ' + args[0])
    return result.stdout


def main():
    if (ROOT / '.owner').read_text().strip() != 'open_agent.ingress.demo.v1':
        raise RuntimeError('Unowned ingress installation')
    container = json.loads(command('docker', 'inspect', 'open-agent-control-identity'))[0]
    if container['Config']['Labels'].get('io.open-agent.owner') != 'open_agent.control.demo.v1':
        raise RuntimeError('Unowned identity service')
    address = container['NetworkSettings']['Networks']['open-agent-control-data']['IPAddress']
    if not ipaddress.ip_address(address).is_private:
        raise RuntimeError('Identity upstream is not private')
    target = ROOT / 'console-routes.caddy'
    if target.is_symlink() or (target.exists() and MARKER not in target.read_text()):
        raise RuntimeError('Unowned console route file')
    routes = f'''{MARKER}
redir /open-agent /open-agent/ 308
handle_path /open-agent/* {{
    root * /opt/open-agent/console/current
    try_files {{path}} /index.html
    header Cache-Control "no-store"
    header Content-Security-Policy "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'"
    file_server
}}
@platform path /platform/* /api/v1/*
handle @platform {{
    header Cache-Control "no-store"
    reverse_proxy 127.0.0.1:18082 {{
        flush_interval -1
        header_up -X-Platform-*
        header_up -X-User-Id
        header_up -X-Tenant-Id
    }}
}}
@identity path /identity /identity/*
handle @identity {{
    reverse_proxy {address}:8000
}}
handle {{
    redir /open-agent/ 302
}}
'''
    previous_routes = target.read_text() if target.exists() else None
    target.write_text(routes)
    target.chmod(0o644)
    config = ROOT / 'Caddyfile'
    original = config.read_text()
    current = original.replace('    handle {\n        respond "Open Agent console is being prepared." 503\n    }',
                               '    import ' + str(target))
    config.write_text(current)
    try:
        command(str(ROOT / 'caddy-2.11.8'), 'validate', '--config', str(config), '--adapter', 'caddyfile')
    except Exception:
        config.write_text(original)
        if previous_routes is not None:
            target.write_text(previous_routes)
        raise
    command('systemctl', 'restart', 'open-agent-ingress')
    print(json.dumps({'origin': 'https://120.26.81.83', 'internal_routes_exposed': False,
                      'user_api_enabled': True, 'console_path': '/open-agent/'}), flush=True)


if __name__ == '__main__':
    main()
