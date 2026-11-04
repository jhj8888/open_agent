"""Provision the explicitly opted-in single-host demo; never a production installer.

Run as root on Linux after copying the verified gVisor archive alongside this file.
Only adds a named runtime, network, firewall rules and project-labelled server.
Existing containers are never stopped. The Docker daemon is reloaded, not restarted.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
from pathlib import Path
import platform
import secrets
import shutil
import subprocess
import tarfile
import time

ROOT = Path("/opt/open-agent/sandbox")
RUNTIME = Path("/opt/open-agent/runtime/gvisor/20261102.0")
NETWORK = "open-agent-sandbox"
BRIDGE = "oa-sandbox0"
SUBNET = "172.30.241.0/24"
SERVER = "open-agent-opensandbox"
LABEL = "io.open-agent.component=opensandbox-demo"
RUNSC_SHA512 = "2e6671bb2d033b871c5811ab57e16ac570988b0e0054c525f911cfaf191bab3ca4efb08e0a98daa4b9e7d248f9e5776ef2ef2f8a6b82d829646a42924c98bb05"
MIRROR = "m.daocloud.io/docker.io/"
SERVER_IMAGE = (
    MIRROR
    + "opensandbox/server@sha256:68ca0212a2749b2c73096ce2ec0264455c64442c45f81007db442f52bf84c9d1"
)
EXECD_IMAGE = (
    MIRROR
    + "opensandbox/execd@sha256:e11fdd6fa641329a1fe5298e71c8dd2afca742674fe48bc855b2ff6715512829"
)


def command(
    *args: str, timeout: int = 60, check: bool = True
) -> subprocess.CompletedProcess:
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if check and result.returncode:
        # Commands never contain credentials. Do not print config files or container environments.
        raise RuntimeError(f"{args[0:3]} failed: {result.stderr[-1800:]}")
    return result


def json_command(*args: str):
    return json.loads(command(*args).stdout)


def install_runtime() -> None:
    archive = ROOT / "gvisor.tar.bz2"
    if hashlib.sha512(archive.read_bytes()).hexdigest() != RUNSC_SHA512:
        raise RuntimeError("gVisor archive checksum mismatch")
    RUNTIME.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:bz2") as source:
        members = source.getmembers()
        if any(
            not (item.isfile() or item.isdir())
            or item.name.startswith("/")
            or ".." in Path(item.name).parts
            for item in members
        ):
            raise RuntimeError("Unexpected gVisor archive layout")
        source.extractall(RUNTIME, members=members, filter="data")
    command(str(RUNTIME / "runsc"), "--version")
    daemon = Path("/etc/docker/daemon.json")
    original = daemon.read_bytes() if daemon.exists() else b"{}"
    config = json.loads(original)
    expected = {
        "path": str(RUNTIME / "runsc"),
        "runtimeArgs": ["--platform=systrap", "--overlay2=all:memory"],
    }
    runtimes = config.setdefault("runtimes", {})
    if "open-agent-runsc" in runtimes and runtimes["open-agent-runsc"] != expected:
        raise RuntimeError("Existing project runtime differs; refusing to overwrite")
    runtimes["open-agent-runsc"] = expected
    candidate = ROOT / "daemon.candidate.json"
    candidate.write_text(json.dumps(config, indent=2) + "\n")
    command("dockerd", "--validate", "--config-file", str(candidate))
    if json.loads(original) != config:
        backup = ROOT / ("daemon.before-" + str(time.time_ns()) + ".json")
        backup.write_bytes(original)
        backup.chmod(0o600)
        shutil.copyfile(candidate, daemon)
        daemon.chmod(0o644)
        command("systemctl", "reload", "docker")
    for _ in range(30):
        info = json_command("docker", "info", "--format", "{{json .Runtimes}}")
        if "open-agent-runsc" in info:
            return
        time.sleep(1)
    raise RuntimeError("Named Docker runtime did not load")


def install_network() -> None:
    result = command("docker", "network", "inspect", NETWORK, check=False)
    if result.returncode:
        selected = ipaddress.ip_network(SUBNET)
        identifiers = command("docker", "network", "ls", "-q").stdout.split()
        for network in json_command("docker", "network", "inspect", *identifiers):
            for item in network.get("IPAM", {}).get("Config") or []:
                if (
                    item.get("Subnet")
                    and ipaddress.ip_network(item["Subnet"]).version == 4
                ):
                    if selected.overlaps(ipaddress.ip_network(item["Subnet"])):
                        raise RuntimeError(
                            "Demo sandbox subnet overlaps existing network"
                        )
        command(
            "docker",
            "network",
            "create",
            "--internal",
            "--subnet",
            SUBNET,
            "--label",
            LABEL,
            "--opt",
            "com.docker.network.bridge.enable_icc=false",
            "--opt",
            "com.docker.network.bridge.name=" + BRIDGE,
            NETWORK,
        )
    network = json_command("docker", "network", "inspect", NETWORK)[0]
    if (
        not network["Internal"]
        or network["EnableIPv6"]
        or network["Labels"].get("io.open-agent.component") != "opensandbox-demo"
    ):
        raise RuntimeError("Unsafe or unowned sandbox network")
    if (
        network["Options"].get("com.docker.network.bridge.enable_icc") != "false"
        or network["Options"].get("com.docker.network.bridge.name") != BRIDGE
    ):
        raise RuntimeError("Sandbox bridge configuration changed")
    # Block sandbox-initiated access to the host itself. Host-initiated SDK replies work.
    rule = [
        "-i",
        BRIDGE,
        "-m",
        "conntrack",
        "!",
        "--ctstate",
        "ESTABLISHED,RELATED",
        "-m",
        "comment",
        "--comment",
        "open-agent-sandbox-host-deny",
        "-j",
        "DROP",
    ]
    if command("iptables", "-w", "5", "-C", "INPUT", *rule, check=False).returncode:
        command("iptables", "-w", "5", "-I", "INPUT", "1", *rule)
    # Explicit project-only forwarding guard remains effective if other application rules change.
    for direction in ["-i", "-o"]:
        rule = [
            direction,
            BRIDGE,
            "-m",
            "comment",
            "--comment",
            "open-agent-sandbox-forward-deny",
            "-j",
            "DROP",
        ]
        if command(
            "iptables", "-w", "5", "-C", "DOCKER-USER", *rule, check=False
        ).returncode:
            command("iptables", "-w", "5", "-I", "DOCKER-USER", "1", *rule)
    # OpenSandbox 1.1.0 publishes execd ports on 0.0.0.0. Cover docker-proxy's
    # host INPUT path as well as Docker DNAT's FORWARD path above.
    rule = [
        "!",
        "-i",
        "lo",
        "-p",
        "tcp",
        "--dport",
        "46000:46499",
        "-m",
        "comment",
        "--comment",
        "open-agent-sandbox-published-deny",
        "-j",
        "DROP",
    ]
    if command("iptables", "-w", "5", "-C", "INPUT", *rule, check=False).returncode:
        command("iptables", "-w", "5", "-I", "INPUT", "1", *rule)
    # Docker 24 has CVE-2024-29018 when upstream DNS uses host loopback. Reject that setup.
    nameservers = [
        line.split()[1]
        for line in Path("/etc/resolv.conf").read_text().splitlines()
        if line.startswith("nameserver ")
    ]
    if not nameservers or any(
        ipaddress.ip_address(ip).is_loopback for ip in nameservers
    ):
        raise RuntimeError(
            "Demo host DNS requires explicit Docker mitigation before use"
        )


def install_server() -> dict:
    for image in [
        SERVER_IMAGE,
        EXECD_IMAGE,
        MIRROR + "library/python:3.12.13-slim-bookworm",
    ]:
        print("Preparing image:", image, flush=True)
        command("docker", "pull", image, timeout=600)
    python_image = json_command(
        "docker", "image", "inspect", MIRROR + "library/python:3.12.13-slim-bookworm"
    )[0]
    digest = next(value for value in python_image["RepoDigests"] if "@sha256:" in value)
    state = ROOT / "state"
    state.mkdir(mode=0o700, exist_ok=True)
    key_path = ROOT / "api-key"
    if not key_path.exists():
        key_path.write_text(secrets.token_urlsafe(48))
        key_path.chmod(0o600)
    key = key_path.read_text().strip()
    if len(key) < 32:
        raise RuntimeError("Sandbox API key is invalid")
    config = f"""[server]
host = "127.0.0.1"
port = 18081
api_key = "{key}"
max_sandbox_timeout_seconds = 3600
[proxy]
resolve_internal = true
[log]
level = "WARNING"
[runtime]
type = "docker"
execd_image = "{EXECD_IMAGE}"
[secure_runtime]
type = "gvisor"
docker_runtime = "open-agent-runsc"
[storage]
allowed_host_paths = ["/opt/open-agent/sandbox/no-tenant-mounts"]
[store]
type = "sqlite"
path = "/var/lib/opensandbox/opensandbox.db"
[docker]
network_mode = "{NETWORK}"
port_range_min = 46000
port_range_max = 46499
drop_capabilities = ["AUDIT_WRITE", "MKNOD", "NET_ADMIN", "NET_RAW", "SYS_ADMIN", "SYS_MODULE", "SYS_PTRACE", "SYS_TIME", "SYS_TTY_CONFIG"]
no_new_privileges = true
pids_limit = 256
sandbox_binds = []
sandbox_env = {{}}
[ingress]
mode = "direct"
[renew_intent]
enabled = false
"""
    config_path = ROOT / "config.toml"
    if config_path.exists() and config_path.read_text() != config:
        raise RuntimeError(
            "Existing server config differs; refusing implicit replacement"
        )
    config_path.write_text(config)
    config_path.chmod(0o600)
    existing = command("docker", "container", "inspect", SERVER, check=False)
    if existing.returncode:
        command(
            "docker",
            "run",
            "-d",
            "--name",
            SERVER,
            "--label",
            LABEL,
            "--restart",
            "unless-stopped",
            "--network",
            "host",
            "--memory",
            "384m",
            "--cpus",
            "0.5",
            "--pids-limit",
            "128",
            "--log-opt",
            "max-size=10m",
            "--log-opt",
            "max-file=2",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--mount",
            "type=bind,source=/var/run/docker.sock,target=/var/run/docker.sock",
            "--mount",
            f"type=bind,source={config_path},target=/etc/opensandbox/config.toml,readonly",
            "--mount",
            f"type=bind,source={state},target=/var/lib/opensandbox",
            SERVER_IMAGE,
        )
    else:
        container = json.loads(existing.stdout)[0]
        if (
            container["Config"]["Labels"].get("io.open-agent.component")
            != "opensandbox-demo"
            or container["Config"]["Image"] != SERVER_IMAGE
        ):
            raise RuntimeError("Unowned or unexpected sandbox server container")
    result = {
        "profile": "gvisor-demo",
        "runtime": "20261102.0",
        "opensandbox": "1.1.0",
        "endpoint": "http://127.0.0.1:18081",
        "runtime_image": digest,
        "server_image": SERVER_IMAGE,
        "execd_image": EXECD_IMAGE,
        "network": NETWORK,
    }
    (ROOT / "deployment.json").write_text(json.dumps(result, indent=2))
    return result


def main() -> None:
    if (
        os.geteuid() != 0
        or platform.machine() != "x86_64"
        or Path(__file__).resolve().parent != ROOT
    ):
        raise RuntimeError(
            "This installer only runs in /opt/open-agent/sandbox on the authorized Linux demo host"
        )
    if tuple(int(part) for part in platform.release().split(".")[:2]) < (5, 6):
        raise RuntimeError("gVisor requires Linux 5.6 or newer")
    ROOT.mkdir(parents=True, exist_ok=True)
    before = set(command("docker", "ps", "-q", "--no-trunc").stdout.split())
    install_runtime()
    install_network()
    result = install_server()
    after = set(command("docker", "ps", "-q", "--no-trunc").stdout.split())
    if not before.issubset(after):
        raise RuntimeError(
            "An existing container stopped during provisioning; inspect before continuing"
        )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
