"""Cold restart persistence, no anonymous trace access, and enforced local-only egress."""

import json
import os
from pathlib import Path
import subprocess
import time
import urllib.request
import urllib.error
import uuid

ROOT = Path("/opt/open-agent/observability")


def call(*args):
    result = subprocess.run(args, text=True, capture_output=True, timeout=45)
    if result.returncode:
        if args[0] == "systemd-run":
            print((result.stdout + result.stderr)[-1500:], flush=True)
        raise RuntimeError("Owned isolation operation failed: " + args[0])
    return result.stdout.strip()


def main():
    os.environ.update(
        MLFLOW_DISABLE_TELEMETRY="true",
        NO_PROXY="*",
        MLFLOW_HTTP_REQUEST_MAX_RETRIES="0",
    )
    credentials = json.loads((ROOT / "backend-secrets.json").read_text())
    os.environ.update(
        MLFLOW_TRACKING_USERNAME=credentials["admin_user"],
        MLFLOW_TRACKING_PASSWORD=credentials["admin_password"],
    )
    from mlflow import MlflowClient

    evidence = json.loads((ROOT / "delivery-verification.json").read_text())
    client = MlflowClient(tracking_uri="http://127.0.0.1:18750")
    trace = client.get_trace(evidence["trace_id"], display=False)
    before = (len(trace.data.spans), trace.info.token_usage)
    for service in ["open-agent-mlflow.service", "open-agent-trace-exporter.service"]:
        unit = Path("/etc/systemd/system") / service
        assert unit.read_text().startswith("# Managed by open_agent observability\n")
        policy = call(
            "systemctl", "show", service, "-p", "IPAddressDeny", "-p", "IPAddressAllow"
        )
        assert "0.0.0.0/0" in policy and "127.0.0.0/8" in policy
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    experiment = json.loads((ROOT / "experiment.json").read_text())["experiment_id"]
    try:
        opener.open(
            "http://127.0.0.1:18750/api/2.0/mlflow/experiments/get?experiment_id="
            + experiment,
            timeout=5,
        )
    except urllib.error.HTTPError as error:
        assert error.code == 401
    else:
        raise AssertionError("Anonymous backend access")
    # cgroup BPF drops packets, which commonly yields timeout rather than EPERM.
    # Compare against a proven reachable listener on this host's non-loopback address.
    import ipaddress

    address = json.loads(call("ip", "-j", "route", "get", "1.1.1.1"))[0]["prefsrc"]
    assert not ipaddress.ip_address(address).is_loopback
    probe = (
        "import socket; s=socket.socket(); s.settimeout(3)\ntry:\n s.connect(("
        + repr(address)
        + ",443))\nexcept (PermissionError,TimeoutError):\n print('denied')\nelse:\n print('connected')\nfinally:\n s.close()"
    )

    def network_probe(restricted):
        args = [
            "systemd-run",
            "--quiet",
            "--wait",
            "--pipe",
            "--collect",
            "--unit=open-agent-observation-probe-" + uuid.uuid4().hex[:8],
            "-p",
            "User=open-agent-traces",
        ]
        if restricted:
            args += ["-p", "IPAddressDeny=any", "-p", "IPAddressAllow=localhost"]
        return call(*args, "/usr/bin/python3", "-c", probe)

    assert network_probe(False) == "connected"
    assert network_probe(True) == "denied"
    assert network_probe(False) == "connected"
    call("systemctl", "restart", "open-agent-mlflow.service")
    for _ in range(60):
        try:
            with opener.open("http://127.0.0.1:18750/health", timeout=2) as response:
                if response.status == 200:
                    break
        except Exception:
            time.sleep(2)
    else:
        raise RuntimeError("Offline backend restart failed")
    after = client.get_trace(evidence["trace_id"], display=False)
    assert before == (len(after.data.spans), after.info.token_usage)
    # Verify all listeners on the dedicated backend port are loopback-only.
    listeners = call("ss", "-ltnH", "sport = :18750")
    assert listeners and all(
        "127.0.0.1:18750" in line for line in listeners.splitlines()
    )
    result = {
        "anonymous_denied": True,
        "backend_loopback_only": True,
        "outbound_denied_by_kernel": True,
        "restart_without_external_network": True,
        "trace_and_usage_survive_restart": True,
        "backend_memory_bytes": int(
            call(
                "systemctl",
                "show",
                "open-agent-mlflow.service",
                "-p",
                "MemoryCurrent",
                "--value",
            )
        ),
        "exporter_memory_bytes": int(
            call(
                "systemctl",
                "show",
                "open-agent-trace-exporter.service",
                "-p",
                "MemoryCurrent",
                "--value",
            )
        ),
    }
    target = ROOT / "isolation-verification.json"
    target.write_text(json.dumps(result, indent=2) + "\n")
    os.chmod(target, 0o600)
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
