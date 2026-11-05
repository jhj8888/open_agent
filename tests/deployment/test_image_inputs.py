"""Artifact integrity and missing-base failures happen before any image build."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

previous = sys.modules.get('fcntl')
if sys.platform == 'win32':
    sys.modules.setdefault('fcntl', SimpleNamespace(LOCK_EX=2, LOCK_NB=4, flock=lambda *_: None))
spec = importlib.util.spec_from_file_location('image_build', Path(__file__).resolve().parents[2] /
    'deploy/platform/images/build_images.py')
build = importlib.util.module_from_spec(spec)
try:
    spec.loader.exec_module(build)
finally:
    if sys.platform == 'win32':
        if previous is None: sys.modules.pop('fcntl', None)
        else: sys.modules['fcntl'] = previous


@pytest.fixture
def inputs(tmp_path):
    sources = tmp_path / 'inputs'
    sources.mkdir()
    for name in ['Dockerfile.api', 'Dockerfile.web', '.dockerignore', 'LICENSE', 'NOTICE', 'LICENSE-caddy']:
        (sources / name).write_bytes(b'test fixture only')
    jar, caddy = tmp_path / 'platform.jar', tmp_path / 'caddy'
    jar.write_bytes(b'reviewed-jar')
    caddy.write_bytes(b'reviewed-caddy')
    console = tmp_path / 'console'
    console.mkdir()
    (console / 'index.html').write_bytes(b'reviewed-static')
    artifacts = {'api_jar_sha256': build.digest(jar), 'caddy_binary_sha256': build.digest(caddy),
                 'caddy_license_sha256': build.digest(sources / 'LICENSE-caddy'),
                 'workbench_files': {'index.html': build.digest(console / 'index.html')},
                 'base_image': 'registry.internal.example/base@sha256:' + 'a' * 64}
    (sources / 'artifacts.json').write_text(json.dumps(artifacts))
    return artifacts, jar, console, caddy, sources


def test_changed_binary_is_rejected(inputs):
    _, jar, _, _, _ = inputs
    jar.write_bytes(b'changed artifact')
    with pytest.raises(RuntimeError, match='binary_checksum'):
        build.verify_inputs(*inputs)


def test_unreviewed_file_is_not_silently_added_to_workbench_image(inputs):
    _, _, console, _, _ = inputs
    (console / '.env').write_text('not-a-real-secret')
    with pytest.raises(RuntimeError, match='asset_manifest'):
        build.verify_inputs(*inputs)


def test_missing_base_never_falls_back_to_a_network_pull(inputs, monkeypatch, tmp_path):
    artifacts, jar, console, caddy, sources = inputs
    commands = Mock(side_effect=RuntimeError('base_missing'))
    monkeypatch.setattr(build, 'command', commands)
    workspace = Mock()
    monkeypatch.setattr(build, 'workspace', workspace)
    with pytest.raises(RuntimeError, match='base_missing'):
        build.build(sources, jar, console, caddy, tmp_path / 'output')
    commands.assert_called_once_with('docker', 'image', 'inspect', artifacts['base_image'])
    workspace.assert_not_called()
