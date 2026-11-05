"""Run synthetic model/ledger and real local Temporal checks in the isolated image.

The test archive is an explicit reviewed fixture, mounted read-only and never
included in the distributed image. Temporal is preverified, never downloaded.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import tarfile
import tempfile
from xml.etree import ElementTree

from build_image import digest, LABEL, OWNER

TEST_FILES = (
    "test_cluster_control.py",
    "test_cluster_process.py",
    "test_cluster_transport.py",
    "test_cluster_supervisor.py",
    "test_cluster_service.py",
    "test_cluster_child.py",
    "test_durable_workflow.py",
    "test_durable_executor.py",
    "test_execution_observations.py",
    "test_deepseek_reasoning.py",
)


def verify(report_path, tests, tests_sha256, temporal, temporal_sha256):
    report = json.loads(report_path.read_bytes())
    identity = report["image"]["local_image_id"]
    image = json.loads(
        subprocess.check_output(["docker", "image", "inspect", identity])
    )[0]
    assert image["Config"]["Labels"][LABEL] == OWNER
    assert digest(tests) == tests_sha256 and digest(temporal) == temporal_sha256
    folder = Path(tempfile.mkdtemp(prefix="image-contracts-", dir=report_path.parent))
    folder.chmod(0o755)
    with tarfile.open(tests, "r:gz") as archive:
        members = archive.getmembers()
        assert len(members) == len(TEST_FILES) and {m.name for m in members} == set(
            TEST_FILES
        )
        for member in members:
            assert member.isfile() and member.size < 1024**2
            path = folder / member.name
            path.write_bytes(archive.extractfile(member).read())
            path.chmod(0o444)
    runner = """import json,subprocess,sys,xml.etree.ElementTree as ET
args=[sys.executable,'-m','pytest','-q','-p','no:cacheprovider','--junitxml=/tmp/checks.xml']+sys.argv[1:]
result=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
print(result.stdout,flush=True)
if result.returncode: sys.exit(result.returncode)
root=ET.parse('/tmp/checks.xml').getroot()
print('IMAGE_CONTRACT_RESULT='+json.dumps({'suites':[s.attrib for s in root.findall('testsuite')]}),flush=True)
"""
    args = [
        "docker",
        "run",
        "--rm",
        "--network=none",
        "--read-only",
        "--user=10001:10001",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--memory=768m",
        "--memory-swap=768m",
        "--cpus=1",
        "--pids-limit=128",
        "--tmpfs=/tmp:rw,nosuid,nodev,size=268435456,mode=1777",
        "--env=TEMPORAL_TEST_BINARY=/opt/temporal",
        "--mount",
        "type=bind,source="
        + str(temporal.resolve())
        + ",target=/opt/temporal,readonly",
        "--mount",
        "type=bind,source="
        + str(folder)
        + ",target=/opt/open-agent/core/agent/tests,readonly",
        identity,
        "-c",
        runner,
        *["/opt/open-agent/core/agent/tests/" + name for name in TEST_FILES],
    ]
    result = subprocess.run(args, capture_output=True, text=True, timeout=600)
    (folder / "pytest.log").write_text(result.stdout + result.stderr)
    if result.returncode:
        raise RuntimeError("Image contracts failed: " + str(folder / "pytest.log"))
    marker = "IMAGE_CONTRACT_RESULT="
    lines = [line for line in result.stdout.splitlines() if line.startswith(marker)]
    assert len(lines) == 1
    checks = json.loads(lines[0][len(marker) :])
    assert all(int(suite["skipped"]) == 0 for suite in checks["suites"])
    evidence = {
        "image_id": identity,
        "test_archive_sha256": tests_sha256,
        "temporal_binary_sha256": temporal_sha256,
        "network": "none",
        "uid": 10001,
        "read_only": True,
        "results": checks,
        "scope": "Official SDKs with synthetic model/control/Kubernetes responses and real local Temporal; not a Kubelet/Kata end-to-end test.",
    }
    (report_path.parent / "contracts-verification.json").write_text(
        json.dumps(evidence, indent=2) + "\n"
    )
    print(json.dumps(evidence), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    for name in ["report", "tests", "temporal"]:
        parser.add_argument("--" + name, required=True, type=Path)
    for name in ["tests-sha256", "temporal-sha256"]:
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    verify(
        args.report, args.tests, args.tests_sha256, args.temporal, args.temporal_sha256
    )
