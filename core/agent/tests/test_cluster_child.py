import json
import os
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from agent.durable import cluster_child
from agent.tests.test_cluster_control import snapshot


@pytest.fixture
def mounted(tmp_path, monkeypatch):
    lease = snapshot()['lease']
    template = {'control_endpoint': 'https://control.internal', 'worker_id': lease['workerId'],
                'worker_key': 'x' * 32, 'model_origins': ['https://model.internal']}
    document = tmp_path / 'worker.json'
    document.write_text(json.dumps(template), encoding='utf8')
    environment = {'OPEN_AGENT_LEASE': json.dumps(lease), 'OPEN_AGENT_POD_UID': str(uuid4())}
    monkeypatch.setattr(cluster_child, 'os', SimpleNamespace(name='posix', geteuid=lambda: 10001, environ=environment))
    monkeypatch.setattr(cluster_child, 'Path', lambda name: tmp_path if name == '/run/open-agent/worker' else Path(name))
    return lease, template, document, environment


def test_projected_worker_template_binds_only_current_lease(mounted):
    lease, template, document, environment = mounted
    settings, record = cluster_child.configuration()
    assert settings.lease_id == record.leaseId == lease['leaseId']
    assert settings.worker_id == record.workerId
    assert settings.sandbox is None
    assert template['worker_key'] not in repr(settings)


@pytest.mark.skipif(os.name != 'posix', reason='Kubernetes projected-volume symlinks require target Linux validation')
def test_real_projected_secret_symlinks_are_accepted_but_cannot_escape_mount(mounted, tmp_path):
    lease, template, document, environment = mounted
    version = tmp_path / '..version'
    version.mkdir()
    document.rename(version / 'worker.json')
    (tmp_path / '..data').symlink_to(version, target_is_directory=True)
    document.symlink_to('..data/worker.json')
    assert cluster_child.configuration()[0].lease_id == lease['leaseId']
    document.unlink()
    # A projected link outside the fixed operator mount must not be read.
    document.symlink_to(tmp_path.parent / 'outside.json')
    (tmp_path.parent / 'outside.json').write_text(json.dumps(template), encoding='utf8')
    with pytest.raises(RuntimeError, match='cluster_executor_configuration_invalid'):
        cluster_child.configuration()


@pytest.mark.parametrize('change', ['root', 'uid', 'oversize', 'wrong_worker', 'lease_override', 'host_sandbox'])
def test_cluster_child_refuses_wrong_identity_and_host_sandbox(mounted, monkeypatch, change):
    lease, template, document, environment = mounted
    if change == 'root': monkeypatch.setattr(cluster_child.os, 'geteuid', lambda: 0)
    if change == 'uid': environment['OPEN_AGENT_POD_UID'] = 'not-a-uid'
    if change == 'oversize': environment['OPEN_AGENT_LEASE'] = 'x' * 4097
    if change == 'wrong_worker': template['worker_id'] = 'someone-else'
    if change == 'lease_override': template['lease_id'] = str(uuid4())
    if change == 'host_sandbox':
        template['sandbox'] = {'endpoint': 'http://127.0.0.1:8080', 'credential': 's' * 32, 'profile': 'kata',
                               'admission_socket': '/run/open-agent/supervisor.sock', 'admission_capability': 'c' * 32}
    document.write_text(json.dumps(template), encoding='utf8')
    with pytest.raises((RuntimeError, ValueError)):
        cluster_child.configuration()
