"""Package SDK lifecycle contracts for isolated Linux verification, not production admission."""
from email.parser import BytesParser
import gzip
from hashlib import sha256
import io
import json
from pathlib import Path
import tarfile
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parents[3]
SOURCES = [
    'agent/__init__.py', 'agent/durable/__init__.py', 'agent/durable/child.py',
    'agent/durable/cluster_child.py', 'agent/durable/cluster_control.py', 'agent/durable/cluster_process.py',
    'agent/durable/cluster_lifecycle.py', 'agent/durable/control.py', 'agent/durable/contracts.py',
    'agent/durable/host_process.py', 'agent/durable/supervision.py', 'agent/tests/__init__.py',
    'agent/tests/test_cluster_control.py', 'agent/tests/test_cluster_process.py',
    'agent/tests/test_cluster_child.py', 'agent/tests/test_cluster_transport.py',
    'agent/durable/cluster_supervisor.py', 'agent/durable/cluster_service.py',
    'agent/durable/workflow.py', 'agent/durable/dispatcher.py',
    'agent/tests/test_cluster_supervisor.py', 'agent/tests/test_cluster_service.py',
    'agent/tests/test_durable_workflow.py',
]
ADDITIONAL = {'aiohttp', 'aiosignal', 'yarl', 'durationpy', 'kubernetes', 'oauthlib', 'requests-oauthlib'}
LICENSES = {'aiohttp': 'Apache-2.0 AND MIT', 'aiosignal': 'Apache-2.0', 'yarl': 'Apache-2.0',
            'durationpy': 'MIT', 'kubernetes': 'Apache-2.0', 'oauthlib': 'BSD-3-Clause', 'requests-oauthlib': 'ISC'}


def build():
    lock = tomllib.loads((ROOT / 'core/agent/uv.lock').read_text(encoding='utf8'))
    packages = {row['name']: row for row in lock['package'] if row['name'] in ADDITIONAL}
    assert packages.keys() == ADDITIONAL
    files, components, payloads = [], [], {}
    for name in ('LICENSE', 'NOTICE'):
        payloads[name] = (ROOT / name).read_bytes()
    for name in SOURCES:
        payloads['source/' + name] = (ROOT / 'core' / name).read_bytes()
    wheels = sorted((ROOT / '.local/cluster-wheels').glob('*.whl'))
    if len(wheels) != len(ADDITIONAL):
        raise ValueError('Unexpected additional wheel set')
    for path in wheels:
        data = path.read_bytes()
        with zipfile.ZipFile(io.BytesIO(data)) as wheel:
            metadata = BytesParser().parsebytes(wheel.read(next(name for name in wheel.namelist() if name.endswith('.dist-info/METADATA'))))
            name = metadata['Name'].lower().replace('_', '-')
            package = packages[name]
            digest = sha256(data).hexdigest()
            if metadata['Version'] != package['version'] or 'sha256:' + digest not in {item['hash'] for item in package['wheels']}:
                raise ValueError('Wheel differs from frozen dependency graph')
            licenses = [name for name in wheel.namelist() if not name.endswith('/') and
                        any(token in Path(name).name.lower() for token in ('license', 'notice', 'copying'))]
            if not licenses:
                raise ValueError('Missing wheel license')
            components.append({'name': name, 'version': package['version'], 'wheel': path.name, 'sha256': digest,
                'reviewed_license': LICENSES[name],
                'license_expression': metadata.get('License-Expression'),
                'license_classifiers': [v for v in metadata.get_all('Classifier', []) if v.startswith('License ::')],
                'license_files': licenses})
        payloads['wheels/' + path.name] = data
    target = ROOT / '.local/cluster-contract.tar.gz'
    with target.open('wb') as raw, gzip.GzipFile(fileobj=raw, mode='wb', filename='', mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode='w') as archive:
            for name, data in sorted(payloads.items()):
                info = tarfile.TarInfo(name)
                info.size, info.mode, info.mtime = len(data), 0o444, 0
                archive.addfile(info, io.BytesIO(data))
                files.append({'path': name, 'size': len(data), 'sha256': sha256(data).hexdigest()})
    result = {'scope': 'Linux contract verification overlay only; not a complete worker image or Kubernetes deployment',
              'sha256': sha256(target.read_bytes()).hexdigest(), 'bytes': target.stat().st_size,
              'base': 'Existing demo worker Python 3.11 frozen environment; unchanged',
              'files': files, 'additional_dependencies': components}
    (ROOT / '.local/cluster-contract-manifest.json').write_bytes((json.dumps(result, indent=2) + '\n').encode())
    (ROOT / 'deploy/platform/cluster/contract-artifacts.json').write_bytes((json.dumps(result, indent=2) + '\n').encode())
    print(json.dumps({'sha256': result['sha256'], 'bytes': result['bytes'], 'files': len(files), 'wheels': len(wheels)}))


if __name__ == '__main__':
    build()
