"""Offline Docker builds from reviewed files; no credentials enter build contexts.

Run on an authorized Linux Docker build host after importing the pinned JRE base.
Docker save archives and local image IDs are NOT registry manifest digests.
"""
import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

OWNER = 'open_agent.platform.images.v1'
LABEL = 'io.open-agent.owner'


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def command(*args, timeout=600):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                            env={**os.environ, 'DOCKER_BUILDKIT': '0'})
    if result.returncode:
        raise RuntimeError('image_command_failed:' + Path(args[0]).name)
    return result.stdout


def verify_inputs(artifacts, jar, console, caddy, sources):
    if jar.is_symlink() or caddy.is_symlink():
        raise RuntimeError('linked_image_binary')
    if digest(jar) != artifacts['api_jar_sha256'] or digest(caddy) != artifacts['caddy_binary_sha256']:
        raise RuntimeError('image_binary_checksum_mismatch')
    if digest(sources / 'LICENSE-caddy') != artifacts['caddy_license_sha256']:
        raise RuntimeError('caddy_license_checksum_mismatch')
    if any(p.is_symlink() for p in console.rglob('*')):
        raise RuntimeError('linked_workbench_asset')
    files = {p.relative_to(console).as_posix(): digest(p) for p in console.rglob('*') if p.is_file()}
    if files != artifacts['workbench_files'] or 'index.html' not in files:
        raise RuntimeError('workbench_asset_manifest_mismatch')
    # Fixed file names only. Neither an entire checkout nor .env is copied.
    for name in ['Dockerfile.api', 'Dockerfile.web', '.dockerignore', 'LICENSE', 'NOTICE']:
        path = sources / name
        if path.is_symlink() or not path.is_file():
            raise RuntimeError('image_source_missing_or_linked')


@contextmanager
def workspace(path):
    if path.is_symlink() or not path.is_absolute():
        raise RuntimeError('image_workspace_invalid')
    if path.exists():
        if path.stat().st_uid != os.geteuid() or path.stat().st_mode & 0o077 or (path / '.owner').read_text() != OWNER:
            raise RuntimeError('image_workspace_not_owned')
    else:
        path.mkdir(mode=0o700, parents=False)
        (path / '.owner').write_text(OWNER)
    fd = os.open(path / '.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def build(sources, jar, console, caddy, output):
    artifacts = json.loads((sources / 'artifacts.json').read_text())
    verify_inputs(artifacts, jar, console, caddy, sources)
    # Fails before building if the base is absent; never runs docker pull.
    base = json.loads(command('docker', 'image', 'inspect', artifacts['base_image']))[0]
    if base['Os'] != 'linux' or base['Architecture'] != 'amd64':
        raise RuntimeError('base_image_platform_mismatch')
    fingerprint = {name: digest(sources / name) for name in ['Dockerfile.api', 'Dockerfile.web', '.dockerignore', 'LICENSE', 'NOTICE', 'LICENSE-caddy', 'artifacts.json']}
    bundle = hashlib.sha256(json.dumps(fingerprint, sort_keys=True).encode()).hexdigest()
    with workspace(output):
        if shutil.disk_usage(output).free < 3 * 1024**3:
            raise RuntimeError('image_build_insufficient_disk')
        context = output / bundle
        context.mkdir(mode=0o700, exist_ok=True)
        if context.is_symlink():
            raise RuntimeError('linked_image_context')
        report = {'schema': 1, 'context_sha256': bundle, 'inputs': artifacts,
                  'source_checksums': fingerprint, 'images': {}, 'base_preloaded': True,
                  'build_network': 'none', 'secrets_in_build_context': False}
        tags = []
        for component in ['api', 'web']:
            directory = context / component
            directory.mkdir(mode=0o700, exist_ok=True)
            if directory.is_symlink():
                raise RuntimeError('linked_image_context')
            for name in ['LICENSE', 'NOTICE', '.dockerignore', 'Dockerfile.' + component]:
                shutil.copyfile(sources / name, directory / name)
                (directory / name).chmod(0o644)
            if component == 'api':
                shutil.copyfile(jar, directory / 'platform.jar')
            else:
                shutil.copyfile(caddy, directory / 'caddy')
                (directory / 'caddy').chmod(0o755)
                shutil.copyfile(sources / 'LICENSE-caddy', directory / 'LICENSE-caddy')
                shutil.copytree(console, directory / 'console', dirs_exist_ok=True)
                for path in (directory / 'console').rglob('*'):
                    path.chmod(0o755 if path.is_dir() else 0o644)
            tag = 'open-agent/platform-' + component + ':' + bundle[:16]
            tags.append(tag)
            existing = subprocess.run(['docker', 'image', 'inspect', tag], capture_output=True, text=True)
            if not existing.returncode:
                found = json.loads(existing.stdout)[0]
                if found['Config'].get('Labels', {}).get(LABEL) != OWNER:
                    raise RuntimeError('image_tag_not_owned')
            command('docker', 'build', '--pull=false', '--network=none', '--platform=linux/amd64',
                    '--build-arg', 'BASE_IMAGE=' + artifacts['base_image'], '--label', LABEL + '=' + OWNER,
                    '--label', 'io.open-agent.context-sha256=' + bundle,
                    '--tag', tag, '--file', str(directory / ('Dockerfile.' + component)), str(directory))
            image = json.loads(command('docker', 'image', 'inspect', tag))[0]
            if image['Config']['User'] != '65531:65531' or image['Os'] != 'linux' or image['Architecture'] != 'amd64':
                raise RuntimeError('built_image_configuration_mismatch')
            report['images'][component] = {'tag': tag, 'local_image_id': image['Id'], 'bytes': image['Size'],
                                           'registry_manifest_digest': None}
            print(json.dumps({'event': 'private_image_built', 'component': component, 'local_image_id': image['Id']}), flush=True)
        archive = output / (bundle + '.docker.tar')
        if archive.is_symlink():
            raise RuntimeError('linked_image_archive')
        command('docker', 'image', 'save', '--output', str(archive), *tags)
        report['archive'] = {'path': str(archive), 'bytes': archive.stat().st_size, 'sha256': digest(archive)}
        (output / 'build-report.json').write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps({'event': 'private_image_archive_ready', **report['archive']}), flush=True)
        return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--sources', type=Path, default=Path(__file__).parent)
    parser.add_argument('--jar', type=Path, required=True)
    parser.add_argument('--console', type=Path, required=True)
    parser.add_argument('--caddy', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    arguments = parser.parse_args()
    os.umask(0o077)
    try:
        build(arguments.sources, arguments.jar, arguments.console.resolve(strict=True), arguments.caddy, arguments.output)
    except Exception as error:
        print(json.dumps({'event': 'private_image_build_failed', 'error_type': type(error).__name__,
                          'code': str(error) if isinstance(error, RuntimeError) else 'unexpected_error'}), flush=True)
        raise SystemExit(1) from None
