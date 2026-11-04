"""Package an already-built preview with its runtime license notices."""
import argparse
import gzip
import hashlib
import io
from pathlib import Path
import tarfile

ROOT = Path(__file__).resolve().parents[3]


def build(output):
    public = ROOT / 'console/frontend/dist-preview'
    if not (public / 'index.html').is_file():
        raise ValueError('Run npm run build:preview in console/frontend first')
    files = [(file, 'public/' + file.relative_to(public).as_posix())
             for file in sorted(public.rglob('*')) if file.is_file()]
    files.append((Path(__file__).with_name('serve.py'), 'serve.py'))
    files.extend((ROOT / name, 'public/' + name) for name in ('LICENSE', 'NOTICE'))
    for package in ('react', 'react-dom', 'scheduler'):
        files.append((ROOT / 'console/frontend/node_modules' / package / 'LICENSE',
                      'public/third-party/' + package + '-LICENSE.txt'))
    if any(file.is_symlink() for file, _ in files):
        raise ValueError('Do not package symlinked files')
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('wb') as stream, gzip.GzipFile(fileobj=stream, mode='wb', filename='', mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode='w') as archive:
            for file, name in sorted(files, key=lambda entry: entry[1]):
                content = file.read_bytes()
                member = tarfile.TarInfo(name)
                member.size = len(content)
                member.mode = 0o644
                archive.addfile(member, io.BytesIO(content))
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    print(digest)
    return digest


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    build(parser.parse_args().output)
