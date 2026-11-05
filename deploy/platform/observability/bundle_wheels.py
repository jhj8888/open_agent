"""Build an offline archive/inventory from official pip-download output; retain wheel notices."""

import argparse
from email.parser import BytesParser
import hashlib
import io
import json
from pathlib import Path
import re
import tarfile
import zipfile

ROOT = Path(__file__).resolve().parents[3]


def main(folder):
    requirements = (
        ROOT / "deploy/platform/observability/requirements.lock"
    ).read_bytes()
    allowed = set(re.findall(r"--hash=sha256:([a-f0-9]{64})", requirements.decode()))
    files = sorted(folder.glob("*.whl"))
    if not files or len(files) > 200:
        raise ValueError("Invalid wheel count")
    manifest = []
    components = []
    target = ROOT / ".local/observation-wheels.tar.gz"
    target.parent.mkdir(mode=0o700, exist_ok=True)
    with tarfile.open(target, "w:gz") as package:
        for path in files:
            data = path.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            if digest not in allowed:
                raise ValueError("Wheel not present in frozen hash lock: " + path.name)
            info = tarfile.TarInfo(path.name)
            info.mode = 0o444
            info.size = len(data)
            info.mtime = 0
            package.addfile(info, io.BytesIO(data))
            manifest.append({"name": path.name, "sha256": digest, "size": len(data)})
            with zipfile.ZipFile(io.BytesIO(data)) as wheel:
                names = wheel.namelist()
                meta = [name for name in names if name.endswith(".dist-info/METADATA")]
                if len(meta) != 1:
                    raise ValueError("Invalid wheel metadata")
                metadata = BytesParser().parsebytes(wheel.read(meta[0]))
                components.append(
                    {
                        "name": metadata["Name"],
                        "version": metadata["Version"],
                        "wheel": path.name,
                        "sha256": digest,
                        "license_expression": metadata.get("License-Expression"),
                        "license_classifiers": [
                            v
                            for v in metadata.get_all("Classifier", [])
                            if v.startswith("License ::")
                        ],
                        "license_files": [
                            {
                                "path": name,
                                "sha256": hashlib.sha256(wheel.read(name)).hexdigest(),
                            }
                            for name in names
                            if not name.endswith("/")
                            and any(
                                word in name.lower().split("/")[-1]
                                for word in ("license", "copying", "notice")
                            )
                        ],
                    }
                )
    result = {
        "requirements_sha256": hashlib.sha256(requirements).hexdigest(),
        "archive_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        "wheels": manifest,
    }
    (ROOT / ".local/observation-wheels-manifest.json").write_text(
        json.dumps(result, indent=2) + "\n"
    )
    inventory = {
        "schema": "open-agent-observation-dependencies/v1",
        "target": "CPython 3.11 Linux x86_64",
        "requirements_sha256": result["requirements_sha256"],
        "components": components,
        "supplemental_notices": [
            {
                "name": "flask-cors",
                "version": "6.0.5",
                "license": "MIT",
                "path": "licenses/Flask-Cors-6.0.5-LICENSE.txt",
                "source": "https://raw.githubusercontent.com/corydolphin/flask-cors/6.0.5/LICENSE",
                "sha256": "134a20d852c0cf9e0f3c228767921b76f4bfc4af4a2fc7c4cc90f6b4fd407b5c",
            }
        ],
    }
    (ROOT / "deploy/platform/observability/dependency-inventory.json").write_bytes(
        (json.dumps(inventory, indent=2) + "\n").encode()
    )
    print(
        json.dumps(
            {
                "wheels": len(files),
                "bytes": target.stat().st_size,
                "archive_sha256": result["archive_sha256"],
            }
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    main(parser.parse_args().directory)
