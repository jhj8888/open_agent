"""Owned private MLflow demo service. Dependency preparation is a separate, connected build step."""

import hashlib
import json
import os
from pathlib import Path
import pwd
import secrets
import subprocess
import time
import urllib.request

ROOT = Path("/opt/open-agent/observability")
DATA = Path("/var/lib/open-agent-mlflow")
MARKER = "# Managed by open_agent observability\n"


def command(*args, timeout=120):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError("Owned observation operation failed: " + args[0])
    return result.stdout


def account(name):
    try:
        return pwd.getpwnam(name)
    except KeyError:
        command(
            "useradd",
            "--system",
            "--no-create-home",
            "--shell",
            "/usr/sbin/nologin",
            name,
        )
        return pwd.getpwnam(name)


def private(path, text, uid=0, gid=0):
    if path.is_symlink():
        raise RuntimeError("Unsafe configuration path")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as stream:
        stream.write(text)
    os.chmod(path, 0o600)
    os.chown(path, uid, gid)


def unit(name, text):
    target = Path("/etc/systemd/system") / name
    if target.is_symlink() or (
        target.exists() and not target.read_text().startswith(MARKER)
    ):
        raise RuntimeError("Refusing unrelated service replacement")
    target.write_text(MARKER + text)
    os.chmod(target, 0o644)


def main():
    if os.geteuid() != 0 or ROOT.is_symlink():
        raise RuntimeError("Invalid owned deployment")
    os.chmod(ROOT, 0o755)
    # This upstream wheel omits a standalone notice; retain the fixed-tag MIT text separately.
    notice = Path(__file__).with_name("Flask-Cors-6.0.5-LICENSE.txt").read_bytes()
    if (
        hashlib.sha256(notice).hexdigest()
        != "134a20d852c0cf9e0f3c228767921b76f4bfc4af4a2fc7c4cc90f6b4fd407b5c"
    ):
        raise RuntimeError("Upstream notice integrity mismatch")
    notices = ROOT / "licenses"
    if notices.is_symlink():
        raise RuntimeError("Unsafe notice path")
    notices.mkdir(mode=0o755, exist_ok=True)
    os.chmod(notices, 0o755)
    target = notices / "Flask-Cors-6.0.5-LICENSE.txt"
    if target.is_symlink():
        raise RuntimeError("Unsafe notice target")
    target.write_bytes(notice)
    os.chmod(target, 0o444)
    prepared = json.loads((ROOT / "runtime-preparation.json").read_text())
    digest = prepared["requirements_sha256"]
    runtime = ROOT / "runtimes" / digest
    if not runtime.is_dir() or prepared["runtime"] != str(runtime):
        raise RuntimeError("Invalid runtime")
    for folder, dirs, files in os.walk(runtime, followlinks=False):
        os.chmod(folder, 0o755)
        for filename in files:
            path = Path(folder) / filename
            if not path.is_symlink():
                os.chmod(path, 0o755 if path.stat().st_mode & 0o111 else 0o644)
    os.chmod(runtime.parent, 0o755)
    service = account("open-agent-mlflow")
    if DATA.is_symlink():
        raise RuntimeError("Unsafe data directory")
    DATA.mkdir(mode=0o700, exist_ok=True)
    os.chown(DATA, service.pw_uid, service.pw_gid)
    config = ROOT / "backend-secrets.json"
    if not config.exists():
        private(
            config,
            json.dumps(
                {
                    "admin_user": "platform-observation-admin",
                    "admin_password": secrets.token_urlsafe(36),
                    "export_user": "platform-observation-exporter",
                    "export_password": secrets.token_urlsafe(36),
                    "flask_key": secrets.token_urlsafe(48),
                }
            ),
        )
    credentials = json.loads(config.read_text())
    private(
        ROOT / "basic-auth.ini",
        f"""[mlflow]
default_permission = NO_PERMISSIONS
database_uri = sqlite:///{DATA}/auth.db
admin_username = {credentials['admin_user']}
authorization_function = mlflow.server.auth:authenticate_request_basic_auth
grant_default_workspace_access = false
""",
        service.pw_uid,
        service.pw_gid,
    )
    environment = {
        "MLFLOW_FLASK_SERVER_SECRET_KEY": credentials["flask_key"],
        "MLFLOW_AUTH_ADMIN_PASSWORD": credentials["admin_password"],
        "MLFLOW_AUTH_CONFIG_PATH": str(ROOT / "basic-auth.ini"),
        "MLFLOW_DISABLE_TELEMETRY": "true",
        "MLFLOW_SERVER_ENABLE_JOB_EXECUTION": "false",
        "MLFLOW_HTTP_REQUEST_MAX_RETRIES": "0",
        "OPENBLAS_NUM_THREADS": "1",
        "OMP_NUM_THREADS": "1",
        "HOME": str(DATA),
        "MPLCONFIGDIR": str(DATA / "matplotlib"),
    }
    private(
        ROOT / "backend.env",
        "".join(k + "=" + v + "\n" for k, v in environment.items()),
    )
    unit(
        "open-agent-mlflow.service",
        f"""[Unit]
Description=Open Agent private observation backend
After=network.target
[Service]
User=open-agent-mlflow
Group=open-agent-mlflow
WorkingDirectory={DATA}
EnvironmentFile={ROOT}/backend.env
ExecStart={runtime}/bin/mlflow server --host 127.0.0.1 --port 18750 --workers 1 --app-name basic-auth --backend-store-uri sqlite:///{DATA}/tracking.db --no-serve-artifacts --default-artifact-root {DATA}/unused-artifacts --allowed-hosts 127.0.0.1:18750,localhost:18750
Restart=on-failure
RestartSec=5
MemoryMax=768M
MemorySwapMax=0
CPUQuota=100%
TasksMax=80
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths={DATA}
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
IPAddressDeny=any
IPAddressAllow=localhost
UMask=0077
[Install]
WantedBy=multi-user.target
""",
    )
    command("systemctl", "daemon-reload")
    command("systemctl", "enable", "open-agent-mlflow.service")
    command("systemctl", "restart", "open-agent-mlflow.service")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for _ in range(90):
        try:
            with opener.open("http://127.0.0.1:18750/health", timeout=2) as response:
                if response.status == 200:
                    break
        except Exception:
            time.sleep(2)
    else:
        raise RuntimeError("MLflow not ready; inspect owned service log")
    result = {
        "mlflow": "3.18.0",
        "requirements_sha256": digest,
        "endpoint": "http://127.0.0.1:18750",
        "public": False,
        "auth": True,
        "outbound_policy": "localhost_only",
        "memory_max_bytes": 805306368,
        "database": "dedicated_sqlite_demo",
        "job_execution": False,
        "telemetry": False,
    }
    private(ROOT / "backend-deployment.json", json.dumps(result, indent=2) + "\n")
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    os.umask(0o077)
    main()
