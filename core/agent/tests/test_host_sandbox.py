import copy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest

from agent.durable import host_sandbox as module
from agent.durable.host_sandbox import DockerSandboxHost, HostSandboxError, admission_app
from agent.durable.supervision import ExecutionSnapshot
from agent.tests.test_durable_supervision import snapshot

IMAGE = 'local/python@sha256:' + 'a' * 64
SERVER = 'local/server@sha256:68ca0212a2749b2c73096ce2ec0264455c64442c45f81007db442f52bf84c9d1'


@pytest.mark.asyncio
async def test_host_command_reads_fragmented_output_to_eof_with_a_hard_limit(monkeypatch):
    process = SimpleNamespace(returncode=0, stdout=SimpleNamespace(read=AsyncMock(side_effect=[b'{"a":', b'1}', b''])), wait=AsyncMock())
    monkeypatch.setattr(module.asyncio, 'create_subprocess_exec', AsyncMock(return_value=process))
    host = DockerSandboxHost(IMAGE, SERVER)
    assert await host.command('docker', 'inspect', 'owned') == '{"a":1}'
    process.stdout.read = AsyncMock(side_effect=[b'x' * 8, b'y' * 3])
    with pytest.raises(HostSandboxError, match='RESPONSE_INVALID'):
        await host.command('docker', 'inspect', 'owned', maximum=10)


def example(phase='created'):
    value = snapshot()
    value.update(phase='observed', invocationId='b' * 32, state='running', sandboxPhase=phase,
                 sandboxId=str(uuid4()) if phase in {'created', 'cleanup_pending'} else None,
                 isolationProfile='gvisor-demo' if phase else None, runtimeImage=IMAGE if phase else None)
    return ExecutionSnapshot.model_validate(value)


def container(value):
    return {'Id': 'c' * 64, 'Name': '/sandbox-' + value.sandboxId, 'State': {'Running': True},
            'Config': {'Image': IMAGE, 'Labels': DockerSandboxHost.scope(value).metadata()},
            'NetworkSettings': {'Networks': {'open-agent-sandbox': {}}}, 'Mounts': [],
            'HostConfig': {'Runtime': 'open-agent-runsc', 'Privileged': False, 'NetworkMode': 'open-agent-sandbox',
                'PidMode': '', 'IpcMode': '', 'UTSMode': '', 'Binds': [], 'Devices': [], 'CapAdd': [],
                'Memory': 384 * 1024 * 1024, 'NanoCpus': 1_000_000_000, 'PidsLimit': 256,
                'RestartPolicy': {'Name': 'no'}, 'SecurityOpt': ['no-new-privileges'],
                'CapDrop': ['NET_ADMIN', 'NET_RAW', 'SYS_ADMIN', 'SYS_PTRACE']}}


@pytest.mark.asyncio
@pytest.mark.parametrize('field,invalid', [('Privileged', True), ('Runtime', 'runc'), ('NetworkMode', 'host'),
    ('PidMode', 'host'), ('IpcMode', 'host'), ('UTSMode', 'host'), ('Binds', ['/etc:/host']),
    ('Devices', ['device']), ('CapAdd', ['SYS_ADMIN']), ('DeviceRequests', ['gpu']), ('Memory', 0),
    ('NanoCpus', 0), ('PidsLimit', -1), ('RestartPolicy', {'Name': 'always'}), ('SecurityOpt', []), ('CapDrop', [])])
async def test_admission_rejects_unsafe_container_before_root_initialization(field, invalid):
    value = example()
    row = container(value)
    row['HostConfig'][field] = invalid
    host = DockerSandboxHost(IMAGE, SERVER)
    host.policy = AsyncMock()
    host.entities = AsyncMock(return_value=[row])
    host.command = AsyncMock()
    with pytest.raises(HostSandboxError):
        await host.admit(value, value.sandboxId)
    host.command.assert_not_awaited()


@pytest.mark.asyncio
async def test_admission_checks_bound_id_and_mounts_and_initializes_only_fixed_uid():
    value = example()
    host = DockerSandboxHost(IMAGE, SERVER)
    host.policy, host.command = AsyncMock(), AsyncMock()
    row = container(value)
    host.entities = AsyncMock(return_value=[row])
    with pytest.raises(HostSandboxError):
        await host.admit(value, str(uuid4()))
    row['Mounts'] = [{'Source': '/host'}]
    with pytest.raises(HostSandboxError):
        await host.admit(value, value.sandboxId)
    row['Mounts'] = []
    await host.admit(value, value.sandboxId)
    args = host.command.call_args.args
    assert args[:5] == ('docker', 'exec', '--user', '0:0', row['Id'])
    assert len(args) == 8 and 'useradd --uid 1000' in args[-1]


@pytest.mark.asyncio
@pytest.mark.parametrize('present', [False, True])
async def test_unknown_creation_never_adopted_or_released_even_when_container_observed(present):
    value = example('creating').model_copy(update={'state': 'failed'})
    observed = value.model_copy(update={'sandboxId': str(uuid4())})
    host = DockerSandboxHost(IMAGE, SERVER)
    host.server, host.command, host.absent = AsyncMock(), AsyncMock(), AsyncMock()
    host.entities = AsyncMock(return_value=[container(observed)] if present else [])
    api = SimpleNamespace(adopt_sandbox=AsyncMock())
    with pytest.raises(HostSandboxError, match='CREATION_UNKNOWN'):
        await host.cleanup(api, value)
    api.adopt_sandbox.assert_not_awaited()
    host.command.assert_not_awaited()
    host.absent.assert_not_awaited()


@pytest.mark.asyncio
async def test_acknowledged_cleanup_removes_exact_container_and_proves_absence_afterward():
    value = example().model_copy(update={'state': 'cancelled'})
    host = DockerSandboxHost(IMAGE, SERVER)
    row = container(value)
    order = []
    host.server = AsyncMock()
    host.entities = AsyncMock(return_value=[row])
    async def command(*args, **kwargs):
        assert args == ('docker', 'rm', '--force', row['Id'])
        order.append('remove')
    async def absent(snapshot):
        assert snapshot == value
        order.append('verify')
    host.command, host.absent = command, absent
    current, evidence = await host.cleanup(None, value)
    assert current == value and order == ['remove', 'verify']
    assert evidence['creationSettled'] is True and evidence['removedContainerId'] == row['Id']


@pytest.mark.asyncio
async def test_mismatched_cleanup_entity_never_removed_and_failed_verification_never_proves_absence():
    value = example().model_copy(update={'state': 'failed'})
    host = DockerSandboxHost(IMAGE, SERVER)
    host.server, host.command = AsyncMock(), AsyncMock()
    row = container(value.model_copy(update={'sandboxId': str(uuid4())}))
    host.entities = AsyncMock(return_value=[row])
    with pytest.raises(HostSandboxError):
        await host.cleanup(None, value)
    host.command.assert_not_awaited()
    host.entities = AsyncMock(return_value=[container(value)])
    host.absent = AsyncMock(side_effect=HostSandboxError('HOST_SANDBOX_STILL_PRESENT'))
    with pytest.raises(HostSandboxError, match='STILL_PRESENT'):
        await host.cleanup(None, value)


@pytest.mark.asyncio
async def test_absence_checks_labels_and_same_name_containers():
    value = example().model_copy(update={'state': 'failed'})
    host = DockerSandboxHost(IMAGE, SERVER)
    host.entities = AsyncMock(return_value=[])
    host.command = AsyncMock(return_value='d' * 64)
    with pytest.raises(HostSandboxError, match='IDENTITY_INVALID'):
        await host.absent(value)
    assert host.command.call_args.args[-1] == 'name=^/sandbox-' + value.sandboxId + '$'


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['capability', 'extra', 'prod', 'cancelled', 'permit', 'image', 'oversized', 'wrong_id'])
async def test_unix_admission_rejects_invalid_capability_scope_input_and_current_permission(monkeypatch, case):
    value = example(None)
    monkeypatch.setattr(module, 'read_capability', lambda *args: 'c' * 40)
    if case == 'prod':
        value = value.model_copy(update={'lease': value.lease.model_copy(update={'scope': value.lease.scope.model_copy(update={'environment': 'prod'})})})
    if case == 'cancelled':
        value = value.model_copy(update={'state': 'cancelled'})
    api = SimpleNamespace(inspect=AsyncMock(return_value=value))
    worker = SimpleNamespace(permit=AsyncMock(return_value=case != 'permit'))
    host = SimpleNamespace(image=IMAGE, policy=AsyncMock(), admit=AsyncMock())
    app = admission_app(api, worker, host, Path('/unused'))
    headers = {'X-Execution-Capability': 'wrong' if case == 'capability' else 'c' * 40}
    body = {'profile': 'gvisor-demo', 'image': 'wrong' if case == 'image' else IMAGE}
    if case in {'extra', 'oversized'}:
        body['untrusted'] = 'x' * (3000 if case == 'oversized' else 1)
    route = '/before'
    if case == 'wrong_id':
        body = {'profile': 'gvisor-demo', 'sandboxId': str(uuid4())}
        route = '/after'
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://supervisor') as client:
        response = await client.post('/v1/attempts/' + value.lease.leaseId + route, json=body, headers=headers)
    assert response.status_code == 403 and response.json() == {'verified': False}
    host.policy.assert_not_awaited()
    host.admit.assert_not_awaited()


@pytest.mark.asyncio
async def test_unix_admission_checks_permission_before_and_after_host_verification(monkeypatch):
    value = example()
    monkeypatch.setattr(module, 'read_capability', lambda *args: 'c' * 40)
    api = SimpleNamespace(inspect=AsyncMock(return_value=value))
    order = []
    async def permit(lease):
        order.append('permit')
        return True
    async def admit(snapshot, sandbox_id):
        assert snapshot == value and sandbox_id == value.sandboxId
        order.append('admit')
    app = admission_app(api, SimpleNamespace(permit=permit), SimpleNamespace(image=IMAGE, admit=admit), Path('/unused'))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://supervisor') as client:
        response = await client.post('/v1/attempts/' + value.lease.leaseId + '/after',
            json={'profile': 'gvisor-demo', 'sandboxId': value.sandboxId}, headers={'X-Execution-Capability': 'c' * 40})
    assert response.status_code == 200 and order == ['permit', 'admit', 'permit']
