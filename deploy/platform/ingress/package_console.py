"""Create a deterministic static-only archive after npm run build:workbench."""

import gzip
import hashlib
import io
import json
from pathlib import Path
import tarfile

ROOT = Path(__file__).resolve().parents[3]


def main():
    source = ROOT / 'console/frontend/dist-workbench'
    output = ROOT / '.local'
    files = sorted(file for file in source.rglob('*') if file.is_file())
    if not files or not (source / 'index.html').is_file():
        raise RuntimeError('Build the workbench before packaging')
    output.mkdir(exist_ok=True)
    package = output / 'workbench.tar.gz'
    checksums = {}
    with package.open('wb') as stream, gzip.GzipFile(fileobj=stream, mode='wb', mtime=0, filename='') as compressed:
        with tarfile.open(fileobj=compressed, mode='w') as archive:
            for file in files:
                name = file.relative_to(source).as_posix()
                if file.is_symlink() or not (name == 'index.html' or name.startswith('assets/')):
                    raise RuntimeError('Unexpected static build file')
                content = file.read_bytes()
                checksums[name] = hashlib.sha256(content).hexdigest()
                info = tarfile.TarInfo(name)
                info.size = len(content)
                info.mode = 0o644
                info.mtime = 0
                archive.addfile(info, io.BytesIO(content))
    manifest = {'archive_sha256': hashlib.sha256(package.read_bytes()).hexdigest(), 'files': checksums}
    (output / 'workbench-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'archive_sha256': manifest['archive_sha256'], 'asset_count': len(files)}))


if __name__ == '__main__':
    main()
