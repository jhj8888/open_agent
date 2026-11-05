"""Install the private, non-executing outbox consumer using official MLflow auth APIs."""

import hashlib
import json
import os
from pathlib import Path
import sys
import tarfile

from provision_backend import ROOT, account, command, private, unit


def bootstrap():
    os.environ.update(MLFLOW_DISABLE_TELEMETRY="true", NO_PROXY="*")
    from mlflow import MlflowClient
    from mlflow.exceptions import RestException
    from mlflow.server import get_app_client

    credentials = json.loads((ROOT / "backend-secrets.json").read_text())
    os.environ.update(
        MLFLOW_TRACKING_USERNAME=credentials["admin_user"],
        MLFLOW_TRACKING_PASSWORD=credentials["admin_password"],
        MLFLOW_DISABLE_TELEMETRY="true",
        MLFLOW_HTTP_REQUEST_MAX_RETRIES="0",
        NO_PROXY="*",
    )
    endpoint = "http://127.0.0.1:18750"
    client = MlflowClient(tracking_uri=endpoint)
    auth = get_app_client("basic-auth", tracking_uri=endpoint)
    try:
        auth.get_user(credentials["export_user"])
    except RestException as error:
        if error.error_code != "RESOURCE_DOES_NOT_EXIST":
            raise RuntimeError("Unexpected auth state") from None
        auth.create_user(credentials["export_user"], credentials["export_password"])
    found = client.get_experiment_by_name("open-agent-private-observations")
    experiment = (
        found.experiment_id
        if found
        else client.create_experiment("open-agent-private-observations")
    )
    auth.grant_user_permission(
        credentials["export_user"], "experiment", experiment, "EDIT"
    )
    private(ROOT / "experiment.json", json.dumps({"experiment_id": experiment}))
    print(
        json.dumps(
            {
                "experiment_id": experiment,
                "export_permission": "EDIT",
                "export_admin": False,
            }
        )
    )


def main():
    if os.geteuid() != 0:
        raise RuntimeError("Owned deployment requires root")
    prepared = json.loads((ROOT / "runtime-preparation.json").read_text())
    runtime = Path(prepared["runtime"])
    if runtime.parent != ROOT / "runtimes":
        raise RuntimeError("Invalid runtime path")
    command(str(runtime / "bin/python"), str(Path(__file__).resolve()), "--bootstrap")
    credentials = json.loads((ROOT / "backend-secrets.json").read_text())
    experiment = json.loads((ROOT / "experiment.json").read_text())["experiment_id"]
    manifest = json.loads(
        Path(__file__).with_name("observation-source-manifest.json").read_text()
    )
    archive = Path(__file__).with_name("observation-source.tar.gz")
    if hashlib.sha256(archive.read_bytes()).hexdigest() != manifest["sha256"]:
        raise RuntimeError("Source archive mismatch")
    source = ROOT / "sources" / manifest["sha256"]
    source.mkdir(parents=True, exist_ok=True, mode=0o755)
    os.chmod(source.parent, 0o755)
    expected = {item["path"]: item["sha256"] for item in manifest["files"]}
    with tarfile.open(archive, "r:gz") as package:
        if set(package.getnames()) != set(expected) or len(package.getmembers()) != 6:
            raise RuntimeError("Invalid exporter source set")
        for member in package.getmembers():
            if (
                not member.isfile()
                or not member.name.startswith("agent/")
                or ".." in member.name
                or member.size > 131072
            ):
                raise RuntimeError("Unsafe source member")
            content = package.extractfile(member).read()
            if hashlib.sha256(content).hexdigest() != expected[member.name]:
                raise RuntimeError("Source member mismatch")
            target = source / member.name
            target.parent.mkdir(parents=True, mode=0o755, exist_ok=True)
            if target.is_symlink() or (
                target.exists() and target.read_bytes() != content
            ):
                raise RuntimeError("Immutable source conflict")
            if not target.exists():
                target.write_bytes(content)
            os.chmod(target, 0o444)
    for folder, _, _ in os.walk(source):
        os.chmod(folder, 0o755)
    user = account("open-agent-traces")
    api_secrets = json.loads(
        Path("/opt/open-agent/control/api-secrets.json").read_text()
    )
    config = {
        "controlEndpoint": "http://127.0.0.1:18082",
        "controlKeyId": "demo-trace-exporter",
        "controlKey": api_secrets["trace_exporter_key"],
        "backend": {
            "endpoint": "http://127.0.0.1:18750/v1/traces",
            "experimentId": experiment,
            "username": credentials["export_user"],
            "password": credentials["export_password"],
        },
    }
    private(ROOT / "exporter-config.json", json.dumps(config), user.pw_uid, user.pw_gid)
    unit(
        "open-agent-trace-exporter.service",
        f"""[Unit]
Description=Open Agent durable trace exporter
After=network.target open-agent-mlflow.service
[Service]
User=open-agent-traces
Group=open-agent-traces
WorkingDirectory={source}
Environment=PYTHONPATH={source}
Environment=PYTHONDONTWRITEBYTECODE=1
Environment=LANGSMITH_TRACING=false
Environment=MLFLOW_DISABLE_TELEMETRY=true
ExecStart={runtime}/bin/python -m agent.observability.exporter --config {ROOT}/exporter-config.json
Restart=on-failure
RestartSec=5
MemoryMax=256M
MemorySwapMax=0
CPUQuota=50%
TasksMax=20
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
IPAddressDeny=any
IPAddressAllow=localhost
UMask=0077
[Install]
WantedBy=multi-user.target
""",
    )
    command("systemctl", "daemon-reload")
    command("systemctl", "enable", "open-agent-trace-exporter.service")
    command("systemctl", "restart", "open-agent-trace-exporter.service")
    result = {
        "source_sha256": manifest["sha256"],
        "runtime_requirements_sha256": prepared["requirements_sha256"],
        "experiment_id": experiment,
        "service_identity": "demo-trace-exporter",
        "memory_max_bytes": 268435456,
        "public": False,
        "outbound_policy": "localhost_only",
    }
    private(ROOT / "exporter-deployment.json", json.dumps(result, indent=2) + "\n")
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    os.umask(0o077)
    if "--bootstrap" in sys.argv:
        bootstrap()
    else:
        main()
