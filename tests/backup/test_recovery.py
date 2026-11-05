"""Maintenance safety contracts; no real service stops in these tests."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import sys
from unittest.mock import Mock

import pytest

# The deployed program is Linux-only; these orchestration tests also run on Windows.
if sys.platform == 'win32':
    original_fcntl = sys.modules.get('fcntl')
    sys.modules.setdefault('fcntl', SimpleNamespace(LOCK_EX=2, LOCK_NB=4, flock=lambda *_: None))
spec = importlib.util.spec_from_file_location('backup_recovery',
    Path(__file__).resolve().parents[2] / 'deploy/platform/backup/recovery.py')
r = importlib.util.module_from_spec(spec)
try:
    spec.loader.exec_module(r)
finally:
    if sys.platform == 'win32':
        if original_fcntl is None:
            sys.modules.pop('fcntl', None)
        else:
            sys.modules['fcntl'] = original_fcntl


@pytest.mark.parametrize('counts', ['1\t0', '0\t1', '', '0\t0\n1'])
def test_idle_gate_rejects_active_missing_and_ambiguous_ledger(monkeypatch, counts):
    monkeypatch.setattr(r, 'sql', lambda _: counts)
    with pytest.raises(RuntimeError, match='requires_idle'):
        r.assert_idle()


def test_admission_race_restores_services_without_creating_snapshot(monkeypatch, tmp_path):
    monkeypatch.setattr(r, 'JOURNAL', tmp_path / 'journal')
    monkeypatch.setattr(r, 'preflight', lambda: ({n: {'State': {'Running': True}} for n in r.CONTAINERS}, []))
    monkeypatch.setattr(r, 'unit_active', lambda _: True)
    commands = Mock()
    resume = Mock()
    archive = Mock()
    monkeypatch.setattr(r, 'run', commands)
    monkeypatch.setattr(r, 'resume', resume)
    monkeypatch.setattr(r, 'restic', archive)
    monkeypatch.setattr(r, 'save', Mock())
    monkeypatch.setattr(r, 'assert_idle', Mock(side_effect=RuntimeError('busy_after_api_stop')))
    with pytest.raises(RuntimeError, match='busy_after_api_stop'):
        r.backup()
    resume.assert_called_once()
    archive.assert_not_called()
    assert commands.call_args_list[0].args[-1] == 'open-agent-control-api'
    assert len(commands.call_args_list) == 1


def test_resume_rejects_journal_targeting_unrelated_service(monkeypatch, tmp_path):
    path = tmp_path / 'maintenance.json'
    path.write_text('{"owner":"' + r.OWNER + '","containers":[],"units":["mysql.service"]}')
    monkeypatch.setattr(r, 'JOURNAL', path)
    monkeypatch.setattr(r, 'installation', Mock())
    commands = Mock()
    monkeypatch.setattr(r, 'run', commands)
    with pytest.raises(RuntimeError, match='scope_mismatch'):
        r.resume()
    commands.assert_not_called()


def test_resume_rejects_foreign_owner(monkeypatch, tmp_path):
    path = tmp_path / 'maintenance.json'
    path.write_text('{"owner":"foreign"}')
    monkeypatch.setattr(r, 'JOURNAL', path)
    monkeypatch.setattr(r, 'installation', Mock())
    with pytest.raises(RuntimeError, match='owner_mismatch'):
        r.resume()


def test_database_fingerprint_never_accepts_shell_database(monkeypatch):
    commands = Mock()
    monkeypatch.setattr(r, 'run', commands)
    with pytest.raises(RuntimeError, match='database_not_owned'):
        r.database_fingerprint('mysql; echo unsafe')
    commands.assert_not_called()


def test_backup_storage_failure_after_cold_stop_always_resumes(monkeypatch, tmp_path):
    root = tmp_path / 'source'
    (root / 'control/releases/current').mkdir(parents=True)
    containers = {n: {'State': {'Running': True}} for n in r.CONTAINERS}
    containers['open-agent-control-api']['Mounts'] = [
        {'Source': str(root / 'control/releases/current/platform.jar'), 'Destination': '/app/platform.jar'}]
    monkeypatch.setattr(r, 'ROOT', root)
    monkeypatch.setattr(r, 'HOME', tmp_path)
    monkeypatch.setattr(r, 'META', root / 'recovery')
    monkeypatch.setattr(r, 'JOURNAL', tmp_path / 'journal')
    monkeypatch.setattr(r, 'preflight', lambda: (containers, []))
    monkeypatch.setattr(r, 'unit_active', lambda _: True)
    monkeypatch.setattr(r, 'save', lambda p, v: p.write_text(__import__('json').dumps(v)))
    monkeypatch.setattr(r, 'assert_idle', Mock())
    monkeypatch.setattr(r, 'owned_directory', Mock())
    monkeypatch.setattr(r, 'database_fingerprint', lambda _: 'digest')
    monkeypatch.setattr(r, 'inspect', lambda _: {'State': {'ExitCode': 0}})
    commands = Mock()
    resume = Mock()
    monkeypatch.setattr(r, 'run', commands)
    monkeypatch.setattr(r, 'resume', resume)
    monkeypatch.setattr(r, 'restic', Mock(side_effect=RuntimeError('storage_unavailable')))
    with pytest.raises(RuntimeError, match='storage_unavailable'):
        r.backup()
    assert any(call.args[-1] == 'open-agent-control-mysql' for call in commands.call_args_list)
    resume.assert_called_once()
    assert not (tmp_path / 'backup-report.json').exists()


def test_resume_attempts_other_services_when_one_dependency_fails(monkeypatch, tmp_path):
    import json
    path = tmp_path / 'journal'
    state = {'owner': r.OWNER, 'containers': ['open-agent-control-mysql', 'open-agent-control-s3'],
             'units': ['open-agent-mlflow.service', 'open-agent-ingress.service']}
    path.write_text(json.dumps(state))
    monkeypatch.setattr(r, 'JOURNAL', path)
    monkeypatch.setattr(r, 'installation', Mock())
    monkeypatch.setattr(r, 'inspect', Mock())
    monkeypatch.setattr(r, 'save', lambda p, v: p.write_text(json.dumps(v)))
    attempted = []
    def command(*args):
        attempted.append(args)
        if args[-1] == 'open-agent-control-mysql':
            raise RuntimeError('unavailable')
    monkeypatch.setattr(r, 'run', command)
    with pytest.raises(RuntimeError, match='resume_incomplete'):
        r.resume()
    assert ('docker', 'start', 'open-agent-control-s3') in attempted
    assert ('systemctl', 'start', 'open-agent-ingress.service') in attempted
    assert json.loads(path.read_text())['phase'] == 'resume_failed'
