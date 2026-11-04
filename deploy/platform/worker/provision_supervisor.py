"""Install only the owned, loopback demo Temporal execution supervisor.

The first deployment supports real model-only runs. Skill execution remains
closed until the independent sandbox verifier is installed. No credentials are
accepted on the command line or printed in deployment evidence.
"""

import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

ROOT = Path('/opt/open-agent/supervisor')
CONTROL = Path('/opt/open-agent/control')
WORKER = Path('/opt/open-agent/agent-worker')
UNIT = Path('/etc/systemd/system/open-agent-supervisor.service')
OWNER = 'open_agent.supervisor.demo.v1'


def command(*args, check=True):
    result = subprocess.run(args, capture_output=True, text=True, timeout=90)
    if check and result.returncode:
        raise RuntimeError('Supervisor provisioning command failed: ' + args[0])
    return result


def write(path, data, mode=0o600):
    if path.is_symlink():
        raise RuntimeError('Linked supervisor installation file')
    temporary = path.with_suffix(path.suffix + '.pending')
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(data.encode())
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def install():
    if os.geteuid() != 0 or ROOT.is_symlink():
        raise RuntimeError('Supervisor installation requires an owned root directory')
    if ROOT.exists() and (not (ROOT / '.owner').exists() or (ROOT / '.owner').read_text().strip() != OWNER):
        raise RuntimeError('Refusing to reuse an unowned supervisor directory')
    ROOT.mkdir(mode=0o700, exist_ok=True)
    ROOT.chmod(0o700)
    if not (ROOT / '.owner').exists():
        write(ROOT / '.owner', OWNER + '\n')
    lock = os.open(ROOT / '.install.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if UNIT.exists() and ('# ' + OWNER) not in UNIT.read_text():
            raise RuntimeError('Refusing to replace an unowned service')
        deployment = json.loads((WORKER / 'deployment.json').read_text())
        source = Path(deployment['source'])
        source.relative_to(WORKER / 'releases')
        if source.is_symlink() or not (source / 'core/agent/durable/service.py').is_file():
            raise RuntimeError('Supervisor source missing')
        python = Path(deployment['environment']) / 'bin/python'
        if str(python) != '/opt/open-agent/agent-worker/venv/bin/python':
            raise RuntimeError('Unexpected executor environment')
        api = json.loads((CONTROL / 'api-deployment.json').read_text())
        if api['loopback_endpoint'] != 'http://127.0.0.1:18082':
            raise RuntimeError('Private control-plane endpoint missing')
        keys = json.loads((CONTROL / 'api-secrets.json').read_text())
        configuration = {
            'control_endpoint': api['loopback_endpoint'],
            'supervisor': {'id': 'demo-supervisor', 'key': keys['reconciler_key']},
            'dispatcher': {'id': 'demo-dispatcher', 'key': keys['dispatcher_key']},
            'worker_template': {
                'control_endpoint': api['loopback_endpoint'], 'worker_id': 'demo-worker',
                'worker_key': keys['worker_key'], 'model_origins': ['https://api.deepseek.com'],
                'model_public_origins': ['https://api.deepseek.com'], 'sandbox': None,
            },
            'source': str(source), 'python': str(python), 'maximum': 2,
        }
        body = json.dumps(configuration, sort_keys=True, indent=2) + '\n'
        config = ROOT / 'service.json'
        unit = f'''# {OWNER}
[Unit]
Description=Open Agent private execution supervisor
After=network-online.target open-agent-temporal.service docker.service
Wants=network-online.target

[Service]
Type=simple
User=root
Group=root
UMask=0077
WorkingDirectory={source}
Environment=PYTHONPATH={source}/core:{source}/core/common
Environment=LANGSMITH_TRACING=false
Environment=LANGCHAIN_TRACING_V2=false
ExecStart={python} -m agent.durable.service {config}
Restart=on-failure
RestartSec=10
TimeoutStopSec=120
KillMode=control-group
MemoryMax=512M
MemorySwapMax=0
CPUQuota=100%
TasksMax=128
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
'''
        changed = not config.exists() or config.read_text() != body or not UNIT.exists() or UNIT.read_text() != unit
        if changed:
            command('systemctl', 'stop', 'open-agent-supervisor.service', check=False)
            write(config, body)
            write(UNIT, unit, 0o644)
            command('systemctl', 'daemon-reload')
        command('systemctl', 'enable', '--now', 'open-agent-supervisor.service')
        for _ in range(60):
            status = command('systemctl', 'show', 'open-agent-supervisor.service', '-p', 'MainPID', '--value').stdout.strip()
            ready = ROOT / 'ready.json'
            if ready.exists():
                state = json.loads(ready.read_text())
                if str(state['pid']) == status and state['source'] == str(source):
                    break
            time.sleep(2)
        else:
            raise RuntimeError('Supervisor readiness timeout; inspect owned service logs')
        result = {'source': str(source), 'source_sha256': deployment['source_sha256'],
                  'configuration_sha256': hashlib.sha256(body.encode()).hexdigest(),
                  'ready': True, 'sandbox_enabled': False, 'maximum': 2,
                  'control_endpoint': api['loopback_endpoint']}
        write(ROOT / 'deployment.json', json.dumps(result, indent=2) + '\n')
        print(json.dumps(result), flush=True)
    finally:
        os.close(lock)


if __name__ == '__main__':
    try:
        install()
    except Exception as error:
        print(json.dumps({'error_type': type(error).__name__, 'message': str(error)}), flush=True)
        raise SystemExit(1) from None
