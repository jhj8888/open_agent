"""Prepare an explicit, hashed offline context; never package the entire checkout."""

import argparse
from email.parser import BytesParser
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import subprocess
import tarfile
import tomllib
from urllib.parse import unquote, urlsplit
import zipfile

from packaging.utils import canonicalize_name, parse_wheel_filename

from download_wheels import select

ROOT = Path(__file__).resolve().parents[3]


def sha(data):
    return hashlib.sha256(data).hexdigest()


def validate_wheels(folder, lock, selected_names):
    allowed = {}
    for item in lock["package"]:
        for wheel in item.get("wheels", []):
            name = unquote(PurePosixPath(urlsplit(wheel["url"]).path).name)
            allowed[(canonicalize_name(item["name"]), item["version"], name)] = wheel[
                "hash"
            ].removeprefix("sha256:")
    inventory, payload, found = [], {}, set()
    for path in sorted(folder.glob("*.whl")):
        if path.is_symlink():
            raise ValueError("Linked wheel")
        name, version, _, _ = parse_wheel_filename(path.name)
        data = path.read_bytes()
        if (
            name in found
            or name not in selected_names
            or allowed.get((name, str(version), path.name)) != sha(data)
        ):
            raise ValueError(
                "Wheel does not match selected package/version and uv.lock: "
                + path.name
            )
        found.add(name)
        payload["wheels/" + path.name] = data
        with zipfile.ZipFile(io.BytesIO(data)) as wheel:
            names = wheel.namelist()
            metadata_files = [
                item for item in names if item.endswith(".dist-info/METADATA")
            ]
            if len(metadata_files) != 1 or len(names) != len(set(names)):
                raise ValueError("Ambiguous wheel metadata")
            metadata = BytesParser().parsebytes(wheel.read(metadata_files[0]))
            if canonicalize_name(metadata["Name"]) != name or metadata[
                "Version"
            ] != str(version):
                raise ValueError("Wheel metadata mismatch")
            notices = []
            for member in names:
                part = PurePosixPath(member)
                if part.is_absolute() or ".." in part.parts or "\\" in member:
                    raise ValueError("Unsafe wheel member")
                if member.endswith("/") or not any(
                    word in part.name.lower()
                    for word in ("license", "licence", "copying", "notice")
                ):
                    continue
                notice = wheel.read(member)
                target = "licenses/wheels/" + name + "/" + member
                payload[target] = notice
                notices.append({"path": target, "sha256": sha(notice)})
            inventory.append(
                {
                    "name": name,
                    "version": str(version),
                    "wheel": path.name,
                    "sha256": sha(data),
                    "license_expression": metadata.get("License-Expression"),
                    "license_classifiers": [
                        value
                        for value in metadata.get_all("Classifier", [])
                        if value.startswith("License ::")
                    ],
                    "license_files": notices,
                }
            )
    if found != set(selected_names):
        raise ValueError(
            "Missing target wheels: " + ",".join(sorted(set(selected_names) - found))
        )
    return payload, inventory


def source_files(root):
    tracked = (
        subprocess.check_output(["git", "-C", str(root), "ls-files", "-z"])
        .decode()
        .split("\0")
    )
    files = {}
    for name in tracked:
        parts = PurePosixPath(name).parts
        if not name.startswith(("core/agent/", "core/common/")) or "tests" in parts:
            continue
        if Path(name).suffix not in {".py", ".toml", ".lock", ".md"} or any(
            part.startswith(".") for part in parts
        ):
            continue
        path = root / name
        if (
            path.is_symlink()
            or not path.is_file()
            or not path.resolve().is_relative_to(root.resolve())
        ):
            raise ValueError("Missing or linked runtime source")
        files["source/" + name] = path.read_bytes()
    for required in [
        "core/agent/durable/cluster_child.py",
        "core/agent/durable/cluster_service.py",
        "core/agent/uv.lock",
    ]:
        if "source/" + required not in files:
            raise ValueError("Incomplete runtime sources")
    for name in ["LICENSE", "NOTICE"]:
        files["licenses/" + name] = (root / name).read_bytes()
    return files


def supplemental_notices(components, folder):
    records = json.loads((folder / "supplemental-notices.json").read_bytes())
    payload = {}
    packages = {item["name"]: item for item in components}
    for record in records:
        package = packages.get(record["name"])
        if package is None or package["version"] != record["version"]:
            raise ValueError("Supplemental license version mismatch")
        for item in record["license_files"]:
            path = PurePosixPath(item["path"])
            if path.is_absolute() or ".." in path.parts or path.parts[0] != "licenses":
                raise ValueError("Unsafe license path")
            source = folder / path
            data = source.read_bytes()
            if source.is_symlink() or sha(data) != item["sha256"]:
                raise ValueError("Supplemental license checksum mismatch")
            payload[item["path"]] = data
            package["license_files"].append(item)
    if any(not item["license_files"] for item in components):
        raise ValueError("Missing package license notices")
    payload["inventory/supplemental-notices.json"] = (
        folder / "supplemental-notices.json"
    ).read_bytes()
    return payload


def package(wheels, requirements, metadata, sbom, output):
    base = json.loads(metadata.read_bytes())
    requirements_bytes = requirements.read_bytes()
    _, names = select(requirements_bytes.decode(), base["environment"])
    lock_bytes = (ROOT / "core/agent/uv.lock").read_bytes()
    payload, components = validate_wheels(
        wheels, tomllib.loads(lock_bytes.decode()), names
    )
    payload.update(supplemental_notices(components, Path(__file__).parent))
    selection = json.loads((wheels / "selection.json").read_bytes())
    if selection["requirements_sha256"] != sha(requirements_bytes) or set(
        selection["selected_packages"]
    ) != set(names):
        raise ValueError("Download provenance mismatch")
    payload.update(source_files(ROOT))
    payload["requirements.lock"] = requirements_bytes
    payload["inventory/resolution.cdx.json"] = sbom.read_bytes()
    payload["inventory/dependencies.json"] = (
        json.dumps(
            {
                "schema": 1,
                "target": base["environment"],
                "uv_lock_sha256": sha(lock_bytes),
                "requirements_sha256": sha(requirements_bytes),
                "scope": "Selected Linux wheels; resolution SBOM includes other platforms. Base OS and bootstrap pip are inventoried at build verification.",
                "components": components,
            },
            indent=2,
        )
        + "\n"
    ).encode()
    for name in ["Dockerfile", ".dockerignore"]:
        payload[name] = (Path(__file__).parent / name).read_bytes()
    manifest = {
        "schema": 1,
        "base": base,
        "source_commit": subprocess.check_output(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True
        ).strip(),
        "source_note": "Source hashes below are authoritative; builder changes may be uncommitted at packaging time.",
        "files": {
            name: {"sha256": sha(data), "bytes": len(data)}
            for name, data in sorted(payload.items())
        },
    }
    manifest_bytes = (json.dumps(manifest, indent=2) + "\n").encode()
    payload["manifest.json"] = manifest_bytes
    if output.exists():
        raise ValueError("Refusing to overwrite an existing delivery archive")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(output, "w:gz") as archive:
        for name, data in sorted(payload.items()):
            info = tarfile.TarInfo(name)
            info.size, info.mode, info.mtime = len(data), 0o644, 0
            archive.addfile(info, io.BytesIO(data))
    result = {
        "schema": 1,
        "archive_sha256": sha(output.read_bytes()),
        "bytes": output.stat().st_size,
        "manifest_sha256": sha(manifest_bytes),
        "files": len(payload),
        "wheels": len(components),
        "missing_wheel_notices": [
            x["name"] for x in components if not x["license_files"]
        ],
    }
    output.with_suffix(".json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    for flag in ["wheels", "requirements", "metadata", "sbom", "output"]:
        parser.add_argument("--" + flag, required=True, type=Path)
    package(**vars(parser.parse_args()))
