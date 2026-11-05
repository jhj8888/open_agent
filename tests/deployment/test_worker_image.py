"""The offline boundary rejects missing/changed inputs before Docker can build."""

import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tarfile
from unittest.mock import Mock
import zipfile

import pytest

FOLDER = Path(__file__).resolve().parents[2] / "deploy/platform/worker_image"
sys.path.insert(0, str(FOLDER))
import build_image as builder
import download_wheels as downloader
import package_inputs as packager

sys.path.pop(0)


def target():
    from packaging.markers import default_environment

    environment = default_environment()
    environment.update(
        implementation_name="cpython",
        implementation_version="3.11.18",
        os_name="posix",
        platform_machine="x86_64",
        platform_system="Linux",
        python_full_version="3.11.18",
        platform_python_implementation="CPython",
        python_version="3.11",
        sys_platform="linux",
    )
    return environment


def test_linux_selection_uses_captured_environment_not_build_host():
    value = "\n".join(
        f"{name}==1.0; {marker} --hash=sha256:" + "a" * 64
        for name, marker in [
            ("linux-only", 'sys_platform == "linux"'),
            ("windows-only", 'sys_platform == "win32"'),
            ("older-python", 'python_full_version < "3.12"'),
            ("newer-python", 'python_full_version >= "3.12"'),
        ]
    )
    selected, names = downloader.select(value, target())
    assert names == ["linux-only", "older-python"]
    assert ";" not in selected  # pip on Windows must not re-evaluate Linux markers.
    assert selected.count("--hash=sha256:") == 2


@pytest.mark.parametrize(
    "value",
    [
        "example>=1",
        "example==1",
        "example @ https://invalid.example/a.whl",
        "example==1 --hash=md5:" + "a" * 32,
        "example==1 --hash=sha256:" + "a" * 64 + " \\",
    ],
)
def test_download_selection_requires_complete_hash_locked_export(value):
    with pytest.raises(ValueError):
        downloader.select(value, target())


def wheel_fixture(tmp_path):
    path = tmp_path / "example-1.0-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "example-1.0.dist-info/METADATA", "Name: example\nVersion: 1.0\n"
        )
        archive.writestr("example-1.0.dist-info/LICENSE", "synthetic fixture license")
    lock = {
        "package": [
            {
                "name": "example",
                "version": "1.0",
                "wheels": [
                    {
                        "url": "https://files.pythonhosted.org/" + path.name,
                        "hash": "sha256:" + packager.sha(path.read_bytes()),
                    }
                ],
            }
        ]
    }
    return path, lock


def test_missing_wheel_fails_instead_of_leaving_online_resolution(tmp_path):
    _, lock = wheel_fixture(tmp_path)
    with pytest.raises(ValueError, match="Missing target wheels"):
        packager.validate_wheels(tmp_path, lock, ["example", "missing"])


def test_wheel_hash_must_belong_to_correct_package_and_version(tmp_path):
    _, lock = wheel_fixture(tmp_path)
    lock["package"][0]["version"] = "2.0"
    with pytest.raises(ValueError, match="does not match"):
        packager.validate_wheels(tmp_path, lock, ["example"])


def test_original_wheel_notices_are_in_distribution(tmp_path):
    _, lock = wheel_fixture(tmp_path)
    payload, packages = packager.validate_wheels(tmp_path, lock, ["example"])
    notice = packages[0]["license_files"][0]
    assert payload[notice["path"]] == b"synthetic fixture license"
    assert packager.sha(payload[notice["path"]]) == notice["sha256"]


def test_changed_supplemental_license_is_rejected(tmp_path):
    (tmp_path / "licenses").mkdir()
    (tmp_path / "licenses/LICENSE").write_bytes(b"changed text")
    record = [
        {
            "name": "example",
            "version": "1.0",
            "license_files": [
                {
                    "path": "licenses/LICENSE",
                    "sha256": packager.sha(b"original license"),
                }
            ],
        }
    ]
    (tmp_path / "supplemental-notices.json").write_text(json.dumps(record))
    with pytest.raises(ValueError, match="checksum mismatch"):
        packager.supplemental_notices(
            [{"name": "example", "version": "1.0", "license_files": []}], tmp_path
        )


def test_license_for_a_different_package_version_is_rejected(tmp_path):
    (tmp_path / "supplemental-notices.json").write_text(
        json.dumps([{"name": "example", "version": "2.0", "license_files": []}])
    )
    with pytest.raises(ValueError, match="version mismatch"):
        packager.supplemental_notices(
            [{"name": "example", "version": "1.0", "license_files": []}], tmp_path
        )


def archive_fixture(tmp_path, *, member_name="Dockerfile", linked=False):
    path = tmp_path / "input.tar.gz"
    content = b"reviewed build input"
    manifest = {
        "schema": 1,
        "files": {
            member_name: {"sha256": packager.sha(content), "bytes": len(content)}
        },
        "base": {"image_id": "sha256:" + "b" * 64},
    }
    with tarfile.open(path, "w:gz") as archive:
        for name, data in [
            (member_name, content),
            ("manifest.json", json.dumps(manifest).encode()),
        ]:
            item = tarfile.TarInfo(name)
            item.size = len(data)
            if linked and name == member_name:
                item.type, item.linkname, item.size = tarfile.SYMTYPE, "/etc/passwd", 0
            archive.addfile(item, io.BytesIO(data))
    return path, builder.digest(path)


@pytest.mark.parametrize(
    "name,linked", [("../outside", False), ("/outside", False), ("Dockerfile", True)]
)
def test_tar_escape_or_link_is_rejected_before_extraction(tmp_path, name, linked):
    archive, digest = archive_fixture(tmp_path, member_name=name, linked=linked)
    with pytest.raises(ValueError, match="Unsafe input"):
        builder.checked_archive(archive, digest)


def test_tampered_delivery_archive_is_rejected(tmp_path):
    archive, digest = archive_fixture(tmp_path)
    archive.write_bytes(archive.read_bytes() + b"changed")
    with pytest.raises(ValueError, match="checksum mismatch"):
        builder.checked_archive(archive, digest)


def test_missing_base_has_no_pull_or_workspace_mutation(tmp_path, monkeypatch):
    archive, digest = archive_fixture(tmp_path)
    command = Mock(side_effect=RuntimeError("base is absent"))
    workspace = Mock()
    monkeypatch.setattr(builder, "command", command)
    monkeypatch.setattr(builder, "workspace", workspace)
    with pytest.raises(RuntimeError, match="base is absent"):
        builder.build(archive, digest, tmp_path / "output")
    command.assert_called_once_with("docker", "image", "inspect", "sha256:" + "b" * 64)
    workspace.assert_not_called()
