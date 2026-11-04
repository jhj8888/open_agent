"""Prepare a versioned Python worker environment without modifying other applications."""

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import pwd
import re
import subprocess
import tarfile

ROOT = Path("/opt/open-agent/agent-worker")
UV = Path("/opt/open-agent/runtime/uv-0.10.9")
UV_WHEEL_SHA256 = "7c9d6deb30edbc22123be75479f99fb476613eaf38a8034c0e98bba24a344179"


def run(args, *, env=None, timeout=900):
    result = subprocess.run(
        args, env=env, capture_output=True, text=True, timeout=timeout
    )
    with (ROOT / "provision.log").open("a") as log:
        log.write(result.stdout + result.stderr)
    if result.returncode:
        raise RuntimeError(
            "Worker dependency preparation failed; inspect the project provision log"
        )
    return result.stdout


def main(
    archive: Path,
    digest: str,
    uv_wheel: Path | None = None,
    download_proxy: str | None = None,
    offline: bool = False,
):
    if os.geteuid() != 0 or not re.fullmatch("[a-f0-9]{64}", digest):
        raise RuntimeError(
            "Requires the authorized demo administrator and a source digest"
        )
    if hashlib.sha256(archive.read_bytes()).hexdigest() != digest:
        raise RuntimeError("Worker source checksum mismatch")
    if download_proxy and not re.fullmatch(
        r"http://127\.0\.0\.1:[0-9]{1,5}", download_proxy
    ):
        raise RuntimeError(
            "Only an explicitly managed local dependency-download proxy is accepted"
        )
    if (
        uv_wheel
        and hashlib.sha256(uv_wheel.read_bytes()).hexdigest() != UV_WHEEL_SHA256
    ):
        raise RuntimeError("Pinned uv wheel checksum mismatch")
    for service in ('open-agent-worker', 'open-agent-supervisor'):
        if subprocess.run(['systemctl', 'is-active', '--quiet', service], check=False).returncode == 0:
            raise RuntimeError("Drain and stop the project's active worker before changing its environment")
    ROOT.mkdir(parents=True, exist_ok=True)
    source = ROOT / "releases" / digest[:16]
    marker = source / ".source-sha256"
    if source.exists():
        if not marker.is_file() or marker.read_text().strip() != digest:
            raise RuntimeError(
                "Refusing to overwrite an unrecognized release directory"
            )
    else:
        source.mkdir(parents=True)
        with tarfile.open(archive, "r:gz") as package:
            members = package.getmembers()
            if (
                len(members) > 5000
                or sum(member.size for member in members) > 80 * 1024 * 1024
            ):
                raise RuntimeError("Worker archive exceeds source limits")
            for member in members:
                path = PurePosixPath(member.name)
                if (
                    not member.isfile()
                    or path.is_absolute()
                    or ".." in path.parts
                    or "\\" in member.name
                    or not (
                        member.name.startswith(("core/agent/", "core/common/"))
                        or member.name in {"LICENSE", "NOTICE"}
                    )
                ):
                    raise RuntimeError("Unexpected worker archive entry")
            for member in members:
                target = source / member.name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(package.extractfile(member).read())
                target.chmod(0o644)
        marker.write_text(digest + "\n")
    if not (UV / "bin/uv").is_file():
        print("Preparing project-local uv 0.10.9", flush=True)
        run(["python3", "-m", "venv", str(UV)], timeout=90)
        run(
            [
                str(UV / "bin/python"),
                "-m",
                "pip",
                "--isolated",
                "install",
                "--disable-pip-version-check",
                "--no-input",
                "--retries",
                "2",
                "--timeout",
                "20",
                *(
                    ["--no-index", str(uv_wheel)]
                    if uv_wheel
                    else ["--index-url", "https://pypi.org/simple", "uv==0.10.9"]
                ),
            ]
        )
    if run([str(UV / "bin/uv"), "--version"], timeout=10).strip() != "uv 0.10.9":
        raise RuntimeError("Unexpected uv version")
    environment = {
        **os.environ,
        "UV_PROJECT_ENVIRONMENT": str(ROOT / "venv"),
        "UV_CACHE_DIR": "/opt/open-agent/cache/uv",
        "UV_PYTHON_DOWNLOADS": "never",
        "UV_HTTP_CONNECT_TIMEOUT": "15",
        "UV_HTTP_TIMEOUT": "60",
        "UV_HTTP_RETRIES": "2",
        "UV_CONCURRENT_DOWNLOADS": "8",
        "UV_NO_PROGRESS": "1",
    }
    if download_proxy:
        environment.update(HTTPS_PROXY=download_proxy, HTTP_PROXY=download_proxy)
    if offline:
        environment['UV_OFFLINE'] = 'true'
    print("Installing the frozen Agent dependency graph", flush=True)
    run(
        [
            str(UV / "bin/uv"),
            "sync",
            "--directory",
            str(source / "core/agent"),
            "--frozen",
            "--no-dev",
            "--python",
            "/usr/bin/python3",
        ],
        env=environment,
    )
    try:
        account = pwd.getpwnam("open-agent-worker")
    except KeyError:
        run(
            [
                "useradd",
                "--system",
                "--no-create-home",
                "--shell",
                "/sbin/nologin",
                "open-agent-worker",
            ],
            timeout=30,
        )
        account = pwd.getpwnam("open-agent-worker")
    state = ROOT / "state"
    state.mkdir(exist_ok=True)
    os.chown(state, account.pw_uid, account.pw_gid)
    state.chmod(0o700)
    versions = json.loads(
        run(
            [
                str(ROOT / "venv/bin/python"),
                "-c",
                "import importlib.metadata as m,json;print(json.dumps({p:m.version(p) for p in ('deepagents','temporalio','opensandbox','langchain-openai')}))",
            ],
            timeout=30,
        )
    )
    report = {
        "source": str(source),
        "source_sha256": digest,
        "environment": str(ROOT / "venv"),
        "versions": versions,
        "service_started": False,
    }
    (ROOT / "deployment.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--uv-wheel", type=Path)
    parser.add_argument("--download-proxy")
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    main(args.archive, args.sha256, args.uv_wheel, args.download_proxy, args.offline)
