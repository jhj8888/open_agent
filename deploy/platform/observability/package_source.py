"""Package only the trusted exporter and its bounded control transport, without local configuration."""

import hashlib
import io
import json
from pathlib import Path
import tarfile

ROOT = Path(__file__).resolve().parents[3]
FILES = [
    "agent/__init__.py",
    "agent/durable/__init__.py",
    "agent/durable/control.py",
    "agent/durable/contracts.py",
    "agent/observability/__init__.py",
    "agent/observability/exporter.py",
]


def main():
    target = ROOT / ".local/observation-source.tar.gz"
    target.parent.mkdir(mode=0o700, exist_ok=True)
    manifest = []
    with tarfile.open(target, "w:gz") as archive:
        for name in FILES:
            data = (ROOT / "core" / name).read_bytes()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o444
            info.mtime = 0
            archive.addfile(info, io.BytesIO(data))
            manifest.append({"path": name, "sha256": hashlib.sha256(data).hexdigest()})
    result = {
        "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        "files": manifest,
    }
    (ROOT / ".local/observation-source-manifest.json").write_text(
        json.dumps(result, indent=2) + "\n"
    )
    print(json.dumps({"sha256": result["sha256"], "files": len(manifest)}))


if __name__ == "__main__":
    main()
