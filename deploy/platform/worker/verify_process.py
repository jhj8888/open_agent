"""Real systemd/cgroup probe; synthetic child only, no model or tenant input.

Place the current host_process.py next to this script before running with the
demo administrator. Creates only UUID-named project units and fixture files.
"""

import asyncio
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
from uuid import uuid4

from host_process import HostProcessError, ProcessBinding, SystemdProcesses, WorkerProgram

CHILD = '''import json, os, pathlib, socket, subprocess, sys, time
def denied_socket(path):
    connection=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
    try:
        connection.connect(path)
        return False
    except OSError as error:
        return error.errno in (2,13)
    finally:
        connection.close()
config=json.loads(pathlib.Path(sys.argv[1]).read_text())
secret=json.loads((pathlib.Path(os.environ['CREDENTIALS_DIRECTORY'])/'worker.json').read_text())
try:
    pathlib.Path(config['source_credential']).read_text()
    source_denied=False
except PermissionError:
    source_denied=True
status=dict(line.split(':',1) for line in pathlib.Path('/proc/self/status').read_text().splitlines() if ':' in line)
child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(180)'])
print('OA_PROCESS_PROBE='+json.dumps({'uid':os.getuid(),'pid':os.getpid(),'child_pid':child.pid,
    'capabilities_empty':int(status['CapEff'].strip(),16)==0,'no_new_privileges':status['NoNewPrivs'].strip()=='1',
    'credential_loaded':secret=={'verification':'synthetic-only'},'source_credential_denied':source_denied,
    'docker_denied':denied_socket('/run/docker.sock'),'system_bus_denied':denied_socket('/run/dbus/system_bus_socket')}),flush=True)
time.sleep(180)
'''


async def main():
    manager = SystemdProcesses()
    binding = ProcessBinding(str(uuid4()), manager.boot_id())
    root = Path('/opt/open-agent/agent-worker/process-verification') / binding.lease_id
    root.mkdir(parents=True, mode=0o755)
    source = root / 'source'
    package = source / 'core/agent/durable'
    package.mkdir(parents=True)
    (package.parent / '__init__.py').write_text('')
    (package / '__init__.py').write_text('')
    (package / 'child.py').write_text(CHILD)
    credentials = root / 'worker.json'
    credentials.write_text('{"verification":"synthetic-only"}\n')
    credentials.chmod(0o600)
    attempt = root / 'attempt.json'
    attempt.write_text(json.dumps({'source_credential': str(credentials)}))
    program = WorkerProgram(Path('/opt/open-agent/agent-worker/venv/bin/python'), source, credentials, attempt)
    invocation = None
    launched = False
    try:
        row = await manager.launch(binding, program)
        launched = True
        invocation = row['InvocationID']
        for _ in range(30):
            result = subprocess.run(['journalctl', '--unit', binding.unit, '--no-pager', '--output=cat'],
                                    capture_output=True, text=True, timeout=10, check=True)
            probes = [line.split('=', 1)[1] for line in result.stdout.splitlines() if line.startswith('OA_PROCESS_PROBE=')]
            if probes:
                child = json.loads(probes[-1])
                break
            await asyncio.sleep(1)
        else:
            raise RuntimeError('Synthetic child did not report; inspect the owned unit journal')
        if child['uid'] == 0 or not all(value for key, value in child.items() if key not in {'pid', 'child_pid', 'uid'}):
            raise RuntimeError('Executor isolation or credential boundary failed')
        try:
            await manager.launch(binding, program)
        except HostProcessError as error:
            assert str(error) == 'HOST_LAUNCH_CONFLICT'
        else:
            raise RuntimeError('Duplicate launch was allowed')
        try:
            await manager.stop(binding, '0' * 32)
        except HostProcessError as error:
            assert str(error) == 'HOST_UNIT_IDENTITY_MISMATCH'
        else:
            raise RuntimeError('Wrong invocation was allowed to stop an executor')
        proof = await manager.stop(binding, invocation)
        for _ in range(20):
            if all(not Path('/proc/' + str(child[key])).exists() for key in ('pid', 'child_pid')):
                break
            await asyncio.sleep(0.2)
        else:
            raise RuntimeError('Executor or its descendant survived group termination')
        replay = await manager.stop(binding, invocation)
        assert replay['workerStopped'] and replay['cgroupEmpty']
        never = ProcessBinding(str(uuid4()), manager.boot_id())
        try:
            await manager.stop(never, None)
        except HostProcessError as error:
            assert str(error) == 'HOST_LAUNCH_UNCONFIRMED'
        else:
            raise RuntimeError('Unknown launch was treated as absent')
        prepared = await manager.stop(never, None, never_launched=True)
        report = {'recorded_at': datetime.now(timezone.utc).isoformat(),
                  'host_process_sha256': hashlib.sha256(Path(__file__).with_name('host_process.py').read_bytes()).hexdigest(),
                  'systemd': subprocess.check_output(['systemctl', '--version'], text=True).splitlines()[0],
                  'actual_non_root_process': child, 'physical_stop': proof,
                  'duplicate_launch_rejected': True, 'foreign_invocation_rejected': True,
                  'descendants_removed': True, 'repeated_stop_confirmed': True, 'unknown_start_keeps_quota': True,
                  'prepared_not_started_confirmed': prepared['kind'] == 'never_launched',
                  'not_verified': ['real RuntimeExecutor or Temporal integration', 'physical sandbox cleanup', 'host reboot']}
        (root / 'verification.json').write_text(json.dumps(report, indent=2) + '\n')
        (root.parent / 'latest.json').write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps(report, indent=2), flush=True)
    finally:
        # Cleanup only the exact UUID unit created by this probe. Retain fixtures and evidence.
        if launched and invocation:
            await manager.stop(binding, invocation)


if __name__ == '__main__':
    asyncio.run(main())
