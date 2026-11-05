"""Offline Linux builder. Only an externally pinned, verified context is accepted."""

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tarfile
import tempfile

OWNER = "open-agent.worker-image.v1"
LABEL = "io.open-agent.owner"


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def checked_archive(archive, expected):
    if (
        archive.is_symlink()
        or not re.fullmatch("[a-f0-9]{64}", expected)
        or digest(archive) != expected
    ):
        raise ValueError("Input archive checksum mismatch")
    with tarfile.open(archive, "r:gz") as source:
        members = source.getmembers()
        names = [item.name for item in members]
        if (
            len(names) != len(set(names))
            or "manifest.json" not in names
            or len(names) > 5000
        ):
            raise ValueError("Ambiguous input archive")
        if sum(item.size for item in members) > 2 * 1024**3:
            raise ValueError("Oversized input archive")
        for member in members:
            path = PurePosixPath(member.name)
            if (
                not member.isfile()
                or path.is_absolute()
                or ".." in path.parts
                or "\\" in member.name
                or str(path) != member.name
            ):
                raise ValueError("Unsafe input member")
        manifest = json.load(source.extractfile("manifest.json"))
        if manifest["schema"] != 1 or set(manifest["files"]) != set(names) - {
            "manifest.json"
        }:
            raise ValueError("Input manifest mismatch")
        for member in members:
            if member.name == "manifest.json":
                continue
            expected_file = manifest["files"][member.name]
            if (
                member.size != expected_file["bytes"]
                or hashlib.file_digest(source.extractfile(member), "sha256").hexdigest()
                != expected_file["sha256"]
            ):
                raise ValueError("Input file checksum mismatch")
        return manifest


@contextmanager
def workspace(path):
    import fcntl

    if not path.is_absolute() or path.is_symlink():
        raise ValueError("Invalid build workspace")
    if path.exists():
        if (
            path.stat().st_uid != os.geteuid()
            or path.stat().st_mode & 0o077
            or (path / ".owner").is_symlink()
            or (path / ".owner").read_text() != OWNER
        ):
            raise ValueError("Unowned build workspace")
    else:
        path.mkdir(mode=0o700)
        (path / ".owner").write_text(OWNER)
    fd = os.open(path / ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def command(*args, timeout=600, log=None):
    result = subprocess.run(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        env={**os.environ, "DOCKER_BUILDKIT": "0"},
    )
    if log is not None:
        log.write_bytes(result.stdout)
    if result.returncode:
        raise RuntimeError("Offline image command failed: " + args[0] + " " + args[1])
    return result.stdout


def inspect_base(manifest):
    # Saved archives may lose RepoDigests; verify the exact immutable local config ID.
    expected = manifest["base"]["image_id"]
    if not re.fullmatch("sha256:[a-f0-9]{64}", expected):
        raise ValueError("Invalid base image ID")
    base = json.loads(command("docker", "image", "inspect", expected))[0]
    if (
        base["Id"] != expected
        or base["Architecture"] != "amd64"
        or base["Os"] != "linux"
    ):
        raise ValueError("Wrong base image")
    return base


def build(archive, expected, output):
    manifest = checked_archive(archive, expected)
    base = inspect_base(manifest)  # Missing base fails before build, never pulls.
    with workspace(output):
        if shutil.disk_usage(output).free < 5 * 1024**3:
            raise ValueError("Insufficient build disk")
        context = Path(tempfile.mkdtemp(prefix=expected[:16] + "-", dir=output))
        # Extract only previously checked regular files, create parents ourselves.
        with tarfile.open(archive, "r:gz") as source:
            for member in source.getmembers():
                destination = context / member.name
                destination.parent.mkdir(parents=True, exist_ok=True)
                with destination.open("xb") as stream:
                    shutil.copyfileobj(source.extractfile(member), stream)
                destination.chmod(0o644)
        # Docker COPY preserves directory modes. The host workspace stays 0700,
        # but public source/license directories inside the image must be traversable.
        for directory in context.rglob("*"):
            if directory.is_dir():
                directory.chmod(0o755)
        # A content-specific local tag prevents an offline builder resolving a registry name.
        base_tag = "open-agent/build-python:" + base["Id"].split(":")[1]
        command("docker", "image", "tag", base["Id"], base_tag)
        if (
            json.loads(command("docker", "image", "inspect", base_tag))[0]["Id"]
            != base["Id"]
        ):
            raise ValueError("Local base alias mismatch")
        tag = "open-agent/cluster-worker:" + expected[:16]
        existing = subprocess.run(
            ["docker", "image", "inspect", tag], capture_output=True
        )
        if (
            not existing.returncode
            and json.loads(existing.stdout)[0]["Config"].get("Labels", {}).get(LABEL)
            != OWNER
        ):
            raise ValueError("Unowned output image tag")
        command(
            "docker",
            "build",
            "--pull=false",
            "--network=none",
            "--platform=linux/amd64",
            "--memory=768m",
            "--memory-swap=768m",
            "--cpu-period=100000",
            "--cpu-quota=100000",
            "--build-arg",
            "BASE_IMAGE=" + base_tag,
            "--label",
            LABEL + "=" + OWNER,
            "--label",
            "io.open-agent.inputs-sha256=" + expected,
            "--tag",
            tag,
            str(context),
            timeout=900,
            log=context / "build.log",
        )
        built = json.loads(command("docker", "image", "inspect", tag))[0]
        if (
            built["Os"] != "linux"
            or built["Architecture"] != "amd64"
            or built["Config"]["User"] != "10001:10001"
        ):
            raise ValueError("Output image identity mismatch")
        image_archive = output / (expected + ".docker.tar")
        base_archive = output / (base["Id"].split(":")[1] + ".base.docker.tar")
        for target, image in [(image_archive, tag), (base_archive, base_tag)]:
            if target.is_symlink():
                raise ValueError("Linked image archive")
            command("docker", "image", "save", "--output", str(target), image)
        result = {
            "schema": 1,
            "builder_sha256": digest(Path(__file__)),
            "input_archive_sha256": expected,
            "context": str(context),
            "base": manifest["base"],
            "build_network": "none",
            "pull": False,
            "image": {
                "tag": tag,
                "local_image_id": built["Id"],
                "size_bytes": built["Size"],
                "registry_manifest_digest": None,
            },
            "archive": {
                "path": str(image_archive),
                "sha256": digest(image_archive),
                "bytes": image_archive.stat().st_size,
            },
            "base_archive": {
                "path": str(base_archive),
                "sha256": digest(base_archive),
                "bytes": base_archive.stat().st_size,
            },
        }
        (output / "build-report.json").write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps(result), flush=True)
        return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--expected", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    build(args.archive, args.expected, args.output)
