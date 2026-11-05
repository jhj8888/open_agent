"""Verify an owned local image/export without connecting to production services."""

import argparse
import json
from pathlib import Path
import subprocess

from build_image import digest, LABEL, OWNER


def verify(report_path, probe):
    report = json.loads(report_path.read_bytes())
    identity = report["image"]["local_image_id"]
    image = json.loads(
        subprocess.check_output(["docker", "image", "inspect", identity])
    )[0]
    assert image["Config"]["Labels"][LABEL] == OWNER
    assert (
        image["Config"]["Labels"]["io.open-agent.inputs-sha256"]
        == report["input_archive_sha256"]
    )
    assert image["Config"]["User"] == "10001:10001"
    assert image["Config"]["Entrypoint"] == ["/opt/venv/bin/python"]
    assert image["Config"]["Cmd"] == ["-m", "agent.durable.cluster_child"]
    for key in ["archive", "base_archive"]:
        assert digest(Path(report[key]["path"])) == report[key]["sha256"]
    subprocess.run(
        ["docker", "image", "load", "--input", report["archive"]["path"]],
        capture_output=True,
        check=True,
    )
    reloaded = json.loads(
        subprocess.check_output(["docker", "image", "inspect", report["image"]["tag"]])
    )[0]
    assert reloaded["Id"] == identity
    args = [
        "docker",
        "run",
        "--rm",
        "--network=none",
        "--read-only",
        "--user=10001:10001",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--memory=512m",
        "--memory-swap=512m",
        "--cpus=1",
        "--pids-limit=64",
        "--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=67108864,mode=1777",
        "--mount",
        "type=bind,source="
        + str(probe.resolve(strict=True))
        + ",target=/probe.py,readonly",
        "--mount",
        "type=bind,source="
        + str((Path(report["context"]) / "wheels").resolve(strict=True))
        + ",target=/wheel-inputs,readonly",
        identity,
    ]
    result = subprocess.run(
        args + ["/probe.py"], capture_output=True, text=True, timeout=120
    )
    if result.returncode:
        (report_path.parent / "runtime-probe.log").write_text(
            result.stdout + result.stderr
        )
        raise RuntimeError("Runtime probe failed; inspect private runtime-probe.log")
    runtime = json.loads(result.stdout)
    # Debian and Python notices stay in the official base. Record installed OS packages.
    os_inventory = subprocess.check_output(
        args
        + [
            "-c",
            'import json,subprocess; print(json.dumps(subprocess.check_output(["dpkg-query","-W","-f=${binary:Package}\\t${Version}\\n"],text=True).splitlines()))',
        ],
        text=True,
    )
    evidence = {
        "schema": 1,
        "image": report["image"],
        "input_archive_sha256": report["input_archive_sha256"],
        "image_archive_sha256": report["archive"]["sha256"],
        "build_network": report["build_network"],
        "probe_network": "none",
        "read_only": True,
        "capabilities": "ALL dropped",
        "runtime": runtime,
        "os_packages": json.loads(os_inventory),
        "load_verification": "Same Docker daemon import and identity check; not an empty-node cold-start test.",
    }
    (report_path.parent / "verification.json").write_text(
        json.dumps(evidence, indent=2) + "\n"
    )
    print(
        json.dumps(
            {
                "verified_image": identity,
                "locked_packages": runtime["locked_packages"],
                "python": runtime["python"],
                "uid": runtime["uid"],
                "read_only": True,
            }
        ),
        flush=True,
    )
    return evidence


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument(
        "--probe", type=Path, default=Path(__file__).with_name("probe_runtime.py")
    )
    args = parser.parse_args()
    verify(args.report, args.probe)
