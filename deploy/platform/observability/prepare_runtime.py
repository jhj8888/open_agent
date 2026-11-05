"""Import a verified wheel bundle and install offline; network staging is explicitly opt-in."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import tarfile
import argparse

ROOT = Path("/opt/open-agent/observability")
UV = "/opt/open-agent/runtime/uv-0.10.9/bin/uv"


def run(*args, timeout=1200):
    result = subprocess.run(
        args,
        text=True,
        capture_output=True,
        timeout=timeout,
        env={
            **os.environ,
            "UV_CACHE_DIR": str(ROOT / "cache"),
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        },
    )
    if result.returncode:
        (ROOT / "prepare-error.log").write_text(result.stdout + result.stderr)
        raise RuntimeError(
            "Observation preparation failed; inspect root-owned prepare-error.log"
        )
    return result.stdout


def main(connected=False):
    if os.geteuid() != 0:
        raise RuntimeError("Owned staging requires root")
    if ROOT.is_symlink():
        raise RuntimeError("Unsafe observation staging path")
    ROOT.mkdir(mode=0o755, exist_ok=True)
    source = Path(__file__).with_name("requirements.lock")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    bundle = ROOT / "bundles" / digest
    bundle.mkdir(mode=0o755, parents=True, exist_ok=True)
    requirements = bundle / "requirements.lock"
    if requirements.exists() and requirements.read_bytes() != source.read_bytes():
        raise RuntimeError("Immutable bundle conflict")
    requirements.write_bytes(source.read_bytes())
    wheels = bundle / "wheels"
    wheels.mkdir(exist_ok=True)
    archive = Path(__file__).with_name("observation-wheels.tar.gz")
    if archive.exists():
        manifest = json.loads(
            Path(__file__).with_name("observation-wheels-manifest.json").read_text()
        )
        if (
            manifest["requirements_sha256"] != digest
            or hashlib.sha256(archive.read_bytes()).hexdigest()
            != manifest["archive_sha256"]
        ):
            raise RuntimeError("Wheel bundle hash mismatch")
        expected = {item["name"]: item for item in manifest["wheels"]}
        if len(expected) > 200:
            raise RuntimeError("Wheel bundle count exceeded")
        with tarfile.open(archive, "r:gz") as package:
            if len(package.getmembers()) != len(expected) or set(
                package.getnames()
            ) != set(expected):
                raise RuntimeError("Invalid wheel members")
            for member in package.getmembers():
                if (
                    not member.isfile()
                    or "/" in member.name
                    or "\\" in member.name
                    or not member.name.endswith(".whl")
                    or member.size > 200 * 1024 * 1024
                ):
                    raise RuntimeError("Unsafe wheel member")
                content = package.extractfile(member).read()
                if (
                    len(content) != expected[member.name]["size"]
                    or hashlib.sha256(content).hexdigest()
                    != expected[member.name]["sha256"]
                ):
                    raise RuntimeError("Wheel integrity mismatch")
                target = wheels / member.name
                if target.is_symlink():
                    raise RuntimeError("Unsafe wheel target")
                target.write_bytes(content)
        print(
            json.dumps({"phase": "verified_offline_bundle", "wheels": len(expected)}),
            flush=True,
        )
    elif connected:
        print(json.dumps({"phase": "connected_staging", "bundle": digest}), flush=True)
        run(
            "python3",
            "-m",
            "pip",
            "download",
            "--index-url",
            "https://pypi.org/simple",
            "--require-hashes",
            "--only-binary=:all:",
            "--no-deps",
            "-r",
            str(requirements),
            "--dest",
            str(wheels),
            timeout=1800,
        )
    venv = ROOT / "runtimes" / digest
    if not venv.exists():
        run(UV, "venv", "--python", "/usr/bin/python3", str(venv), timeout=30)
    print(
        json.dumps(
            {"phase": "offline_install", "wheels": len(list(wheels.glob("*.whl")))}
        ),
        flush=True,
    )
    run(
        UV,
        "pip",
        "sync",
        "--python",
        str(venv / "bin/python"),
        "--offline",
        "--no-index",
        "--find-links",
        str(wheels),
        "--require-hashes",
        str(requirements),
        timeout=600,
    )
    result = {
        "requirements_sha256": digest,
        "runtime": str(venv),
        "installer": "uv 0.10.9",
        "offline_install": True,
        "wheels": [
            {
                "name": p.name,
                "size": p.stat().st_size,
                "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
            }
            for p in sorted(wheels.glob("*.whl"))
        ],
    }
    (bundle / "manifest.json").write_text(json.dumps(result, indent=2) + "\n")
    (ROOT / "runtime-preparation.json").write_text(json.dumps(result, indent=2) + "\n")
    print(
        json.dumps(
            {
                "phase": "prepared",
                "requirements_sha256": digest,
                "wheel_count": len(result["wheels"]),
                "bytes": sum(w["size"] for w in result["wheels"]),
                "offline_install": True,
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    parser.add_argument("--connected", action="store_true")
    main(parser.parse_args().connected)
