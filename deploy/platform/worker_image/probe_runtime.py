"""Run inside the image with network=none, UID 10001 and a read-only root."""

import importlib
from importlib import metadata
import json
import os
from pathlib import Path
import platform
import subprocess
import sys


def main():
    assert os.geteuid() == 10001 and os.getegid() == 10001
    imports = [
        "deepagents",
        "kubernetes",
        "temporalio",
        "opensandbox",
        "agent.durable.executor",
        "agent.durable.cluster_child",
        "agent.durable.cluster_service",
        "agent.engine.observations",
    ]
    for name in imports:
        importlib.import_module(name)
    denied = []
    for path in [
        "/opt/open-agent/write-probe",
        "/opt/venv/write-probe",
        "/root/write-probe",
    ]:
        try:
            Path(path).write_text("probe")
        except OSError:
            denied.append(path)
        else:
            raise AssertionError("Image runtime unexpectedly writable")
    assert not Path("/run/open-agent").exists()
    assert not Path("/var/run/secrets/kubernetes.io/serviceaccount/token").exists()
    check = subprocess.run(
        [sys.executable, "-m", "pip", "check"], capture_output=True, text=True
    )
    assert check.returncode == 0, check.stdout
    expected = json.loads(
        Path("/opt/open-agent/inventory/dependencies.json").read_bytes()
    )
    for item in expected["components"]:
        assert metadata.version(item["name"]) == item["version"], item["name"]
    sample = next(
        item for item in expected["components"] if item["name"] == "packaging"
    )
    Path("/tmp/empty-wheels").mkdir()
    install_failures = {}
    for case, folder, checksum, message in [
        ("missing", "/tmp/empty-wheels", sample["sha256"], "No matching distribution"),
        ("changed_hash", "/wheel-inputs", "a" * 64, "DO NOT MATCH THE HASHES"),
    ]:
        requirement = Path("/tmp/" + case + ".lock")
        requirement.write_text(
            "packaging==" + sample["version"] + " --hash=sha256:" + checksum + "\n"
        )
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--no-cache-dir",
                "--no-index",
                "--no-deps",
                "--only-binary=:all:",
                "--require-hashes",
                "--ignore-installed",
                "--find-links=" + folder,
                "--target=/tmp/" + case + "-install",
                "-r",
                str(requirement),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode != 0 and message in result.stdout + result.stderr
        assert not Path("/tmp/" + case + "-install/packaging").exists()
        install_failures[case] = {"exit_code": result.returncode, "installed": False}
    packages = sorted(
        [
            {"name": item.metadata["Name"], "version": item.version}
            for item in metadata.distributions()
        ],
        key=lambda item: item["name"].lower(),
    )
    # No runtime configuration is mounted: both entry points must fail closed.
    entries = {}
    for module in ["agent.durable.cluster_child", "agent.durable.cluster_service"]:
        run = subprocess.run(
            [sys.executable, "-m", module], capture_output=True, text=True, timeout=30
        )
        assert run.returncode != 0 and "ModuleNotFoundError" not in run.stderr, module
        entries[module] = {"missing_configuration_exit_code": run.returncode}
    report = {
        "python": platform.python_version(),
        "uid": os.geteuid(),
        "gid": os.getegid(),
        "imports": imports,
        "read_only_checks": denied,
        "pip_check": check.stdout.strip(),
        "locked_packages": len(expected["components"]),
        "offline_install_failures": install_failures,
        "installed_packages": packages,
        "entrypoints": entries,
    }
    print(json.dumps(report), flush=True)


if __name__ == "__main__":
    main()
