"""Verify an immutable SDK overlay on the demo host without changing its live worker."""
from datetime import datetime, timezone
import fcntl
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import tarfile
import xml.etree.ElementTree as ET

ROOT = Path('/opt/open-agent/cluster-contracts')
PYTHON = Path('/opt/open-agent/agent-worker/venv/bin/python')
UV = Path('/opt/open-agent/runtime/uv-0.10.9/bin/uv')
OWNER = 'open-agent-cluster-contracts-v1'


def run(args, *, environment=None, timeout=180):
    result = subprocess.run([str(arg) for arg in args], env=environment, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, timeout=timeout)
    if result.returncode:
        raise RuntimeError('cluster_contract_command_failed')
    return result.stdout


def main(bundle, manifest):
    os.umask(0o077)
    if os.geteuid() != 0 or ROOT.is_symlink() or ROOT.parent.resolve() != Path('/opt/open-agent'):
        raise RuntimeError('cluster_contract_path_invalid')
    if ROOT.exists():
        if not (ROOT / 'owner').is_file() or (ROOT / 'owner').read_text() != OWNER:
            raise RuntimeError('cluster_contract_ownership_invalid')
    else:
        ROOT.mkdir(mode=0o700)
        (ROOT / 'owner').write_text(OWNER)
    with (ROOT / 'lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        data = json.loads(manifest.read_text())
        if sha256(bundle.read_bytes()).hexdigest() != data['sha256']:
            raise RuntimeError('cluster_contract_archive_changed')
        folder = ROOT / data['sha256']
        if folder.is_symlink() or folder.parent.resolve() != ROOT.resolve():
            raise RuntimeError('cluster_contract_path_invalid')
        folder.mkdir(mode=0o700, exist_ok=True)
        expected = {row['path']: row for row in data['files']}
        if len(expected) != len(data['files']) or len(expected) > 100:
            raise RuntimeError('cluster_contract_manifest_invalid')
        with tarfile.open(bundle, 'r:gz') as archive:
            members = archive.getmembers()
            if {row.name for row in members} != expected.keys() or len(members) != len(expected):
                raise RuntimeError('cluster_contract_members_invalid')
            for member in members:
                relative = PurePosixPath(member.name)
                if not member.isfile() or relative.is_absolute() or '..' in relative.parts or member.size > 30000000:
                    raise RuntimeError('cluster_contract_member_invalid')
                content = archive.extractfile(member).read()
                record = expected[member.name]
                if len(content) != record['size'] or sha256(content).hexdigest() != record['sha256']:
                    raise RuntimeError('cluster_contract_member_changed')
                target = folder / relative
                if target.is_symlink() or not target.resolve().is_relative_to(folder):
                    raise RuntimeError('cluster_contract_path_invalid')
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                if target.exists():
                    if target.read_bytes() != content:
                        raise RuntimeError('cluster_contract_existing_file_changed')
                else:
                    with target.open('xb') as stream:
                        stream.write(content)
                    target.chmod(0o444)
        vendor = folder / 'vendor'
        version_script = ('from importlib import metadata as m;import json;'
            'print(json.dumps({name:m.version(name) for name in '
            + repr([p['name'] for p in data['additional_dependencies']]) + ' if m.packages_distributions().get(name.replace("-","_"))}))')
        base_environment = {key: value for key, value in os.environ.items() if key != 'PYTHONPATH'}
        base_before = json.loads(run([PYTHON, '-c', version_script], environment=base_environment))
        environment = {**os.environ, 'UV_OFFLINE': 'true', 'UV_PYTHON_DOWNLOADS': 'never',
                       'PYTHONDONTWRITEBYTECODE': '1', 'LANGSMITH_TRACING': 'false', 'LANGCHAIN_TRACING_V2': 'false',
                       'PYTHONPATH': str(folder / 'source') + ':' + str(vendor)}
        if vendor.is_symlink():
            raise RuntimeError('cluster_contract_path_invalid')
        if not vendor.exists():
            run([UV, 'pip', 'install', '--python', PYTHON, '--target', vendor, '--offline', '--no-index',
                 '--no-deps', '--link-mode', 'copy', *sorted((folder / 'wheels').glob('*.whl'))], environment=environment)
        versions = json.loads(run([PYTHON, '-c', 'from importlib import metadata as m;import json;'
            'print(json.dumps({name:m.version(name) for name in ' + repr([p['name'] for p in data['additional_dependencies']]) + '}))'], environment=environment))
        if versions != {row['name']: row['version'] for row in data['additional_dependencies']}:
            raise RuntimeError('cluster_contract_dependency_mismatch')
        tests = [str(folder / 'source/agent/tests' / name) for name in
                 ['test_cluster_control.py', 'test_cluster_process.py', 'test_cluster_child.py', 'test_cluster_transport.py']]
        xml = folder / 'pytest.xml'
        result = subprocess.run([str(PYTHON), '-m', 'pytest', *tests, '-q', '-p', 'no:cacheprovider', '--tb=short', '--junitxml=' + str(xml)],
                                env=environment, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=180)
        (folder / 'pytest.log').write_bytes(result.stdout)
        if result.returncode:
            raise RuntimeError('cluster_contract_tests_failed')
        suites = ET.parse(xml).getroot().findall('testsuite')
        count = sum(int(s.attrib['tests']) for s in suites)
        if not count or any(int(s.attrib[key]) for s in suites for key in ['failures', 'errors', 'skipped']):
            raise RuntimeError('cluster_contract_tests_incomplete')
        base_after = json.loads(run([PYTHON, '-c', version_script], environment=base_environment))
        if base_before != base_after:
            raise RuntimeError('live_worker_dependencies_changed')
        report = {'checked_at': datetime.now(timezone.utc).isoformat(), 'archive_sha256': data['sha256'],
                  'tests_passed': count, 'failures': 0, 'errors': 0, 'skipped': 0, 'dependencies': versions,
                  'offline_overlay_install': True, 'live_worker_environment_modified': False,
                  'live_worker_dependencies_before_and_after': base_before,
                  'https_loopback_verified': True, 'projected_secret_symlinks_verified': True,
                  'kubernetes_api_and_kubelet_verified': False, 'kata_and_cni_verified': False}
        (folder / 'verification.json').write_text(json.dumps(report, indent=2) + '\n')
        (ROOT / 'latest-verification.json').write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps(report))


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('bundle', type=Path)
    parser.add_argument('manifest', type=Path)
    args = parser.parse_args()
    main(args.bundle, args.manifest)
