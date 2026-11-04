"""Publish a reviewed static workbench archive by digest; never replace other apps."""

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import tarfile
import time

ROOT = Path('/opt/open-agent/console')
OWNER = 'open_agent.console.demo.v1'


def main():
    if os.geteuid() != 0 or ROOT.is_symlink():
        raise RuntimeError('Console publication requires an owned root directory')
    if ROOT.exists() and (ROOT / '.owner').read_text().strip() != OWNER:
        raise RuntimeError('Refusing unowned console directory')
    package = Path(__file__).parent / 'workbench.tar.gz'
    manifest = json.loads((Path(__file__).parent / 'workbench-manifest.json').read_text())
    digest = hashlib.sha256(package.read_bytes()).hexdigest()
    if digest != manifest['archive_sha256']:
        raise RuntimeError('Console archive digest mismatch')
    contents = {}
    with tarfile.open(package, 'r:gz') as archive:
        for member in archive.getmembers():
            path = PurePosixPath(member.name)
            if (not member.isfile() or path.is_absolute() or '..' in path.parts or
                    str(path) != member.name or '\\' in member.name or member.size > 10 * 1024 * 1024 or
                    member.name in contents or not (member.name == 'index.html' or member.name.startswith('assets/'))):
                raise RuntimeError('Unexpected console archive entry')
            contents[member.name] = archive.extractfile(member).read()
            if len(contents) > 200:
                raise RuntimeError('Too many console assets')
    if 'index.html' not in contents or {name: hashlib.sha256(body).hexdigest() for name, body in contents.items()} != manifest['files']:
        raise RuntimeError('Console assets do not match review manifest')
    ROOT.mkdir(mode=0o755, exist_ok=True)
    (ROOT / '.owner').write_text(OWNER + '\n')
    (ROOT / '.owner').chmod(0o600)
    releases = ROOT / 'releases'
    if releases.is_symlink():
        raise RuntimeError('Linked release directory')
    releases.mkdir(mode=0o755, exist_ok=True)
    target = releases / digest
    if target.is_symlink():
        raise RuntimeError('Linked console release')
    target.mkdir(mode=0o755, exist_ok=True)
    for name, body in contents.items():
        path = target.joinpath(*PurePosixPath(name).parts)
        if any(part.is_symlink() for part in [path, *path.parents] if part != ROOT.parent):
            raise RuntimeError('Linked asset destination')
        path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        if path.exists() and path.read_bytes() != body:
            raise RuntimeError('Immutable console asset differs')
        if not path.exists():
            with path.open('xb') as output:
                output.write(body)
                output.flush()
                os.fsync(output.fileno())
        path.chmod(0o644)
    current = ROOT / 'current'
    previous = None
    if current.is_symlink():
        current.resolve(strict=True).relative_to(releases.resolve())
        previous = current.resolve().name
    elif current.exists():
        raise RuntimeError('Refusing non-link console current path')
    staging = ROOT / 'current.pending'
    if staging.exists() or staging.is_symlink():
        raise RuntimeError('Incomplete console switch exists; inspect before retry')
    staging.symlink_to(target, target_is_directory=True)
    os.replace(staging, current)
    result = {'owner': OWNER, 'archive_sha256': digest, 'files': manifest['files'],
              'previous': previous, 'published_unix': int(time.time()),
              'url': 'https://120.26.81.83/open-agent/', 'browser_verified': False}
    (ROOT / 'deployment.json').write_text(json.dumps(result, indent=2) + '\n')
    (ROOT / 'deployment.json').chmod(0o600)
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
