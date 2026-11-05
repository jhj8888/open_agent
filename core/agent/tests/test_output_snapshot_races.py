"""Linux filesystem race checks; real reader/pipe, no mocked stat/read calls."""
import ast
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

if sys.platform != 'linux':
    pytest.skip('Linux openat and pipe backpressure checks', allow_module_level=True)
import fcntl

SOURCE = Path(os.environ.get('OUTPUT_SNAPSHOT_SOURCE',
    str(Path(__file__).parents[1] / 'platform_runtime/output_snapshot.py')))
TREE = ast.parse(SOURCE.read_text())
if os.environ.get('OUTPUT_SNAPSHOT_SHA256'):
    assert hashlib.sha256(SOURCE.read_bytes()).hexdigest() == os.environ['OUTPUT_SNAPSHOT_SHA256']
READER = next(ast.literal_eval(node.value) for node in TREE.body if isinstance(node, ast.Assign)
              and any(isinstance(target, ast.Name) and target.id == 'READER' for target in node.targets))


def reader(folder):
    # Redirect only the fixed root components to this test's private directory.
    # The exact production reader logic and declared output paths are unchanged.
    assert READER.count('["workspace","outputs"]') == 1
    script = READER.replace('["workspace","outputs"]', repr(list(folder.parts[1:])))
    return subprocess.Popen([sys.executable, '-I', '-c', script], stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)


@pytest.mark.parametrize('change', ['append', 'truncate', 'overwrite_restore_mtime'])
def test_mutation_after_open_before_read_completion_is_rejected(tmp_path, change):
    content = b'A' * (4 * 1024 * 1024)
    path = tmp_path / 'report.bin'
    for _ in range(10):
        path.write_bytes(content)
        original = path.stat()
        process = reader(tmp_path)
        try:
            assert fcntl.fcntl(process.stdout.fileno(), fcntl.F_GETPIPE_SZ) < len(content)//4
            descriptor = json.loads(process.stdout.readline())
            assert descriptor['size'] == len(content)
            assert process.stdout.read(1) == b'A'
            # The pipe cannot hold the remaining bytes: the real reader is still
            # in its read/write loop after its initial fstat, before final fstat.
            assert process.poll() is None
            with path.open('r+b') as writer:
                if change == 'append':
                    writer.seek(0, 2); writer.write(b'new bytes')
                elif change == 'truncate':
                    writer.truncate(1024)
                else:
                    writer.seek(2*1024*1024); writer.write(b'B' * 65536)
                writer.flush(); os.fsync(writer.fileno())
            if change == 'overwrite_restore_mtime':
                os.utime(path, ns=(original.st_atime_ns, original.st_mtime_ns))
            output, error = process.communicate(timeout=10)
            assert process.returncode != 0 and b'output_snapshot_rejected' in error
            assert len(output) <= len(content)-1
        finally:
            if process.poll() is None: process.kill()
            process.communicate(timeout=5)


def test_stable_file_has_exact_content_and_digest(tmp_path):
    content = bytes(range(256)) * 16384
    (tmp_path / 'report.bin').write_bytes(content)
    process = reader(tmp_path)
    output, error = process.communicate(timeout=10)
    assert process.returncode == 0 and not error
    header, body = output.split(b'\n', 1)
    assert json.loads(header) == {'path':'/workspace/outputs/report.bin','size':len(content)}
    assert hashlib.sha256(body).digest() == hashlib.sha256(content).digest()
