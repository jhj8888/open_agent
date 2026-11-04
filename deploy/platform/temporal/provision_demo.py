"""Install the pinned official Temporal development server for this project's demo.

Production requires the separate HA/TLS/persistence deployment; this is a single
host SQLite-backed service bound to loopback, with no public UI or cloud access.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import pwd
import socket
import subprocess
import tarfile
import time

VERSION = "1.10.0"
SHA256 = "521f461f3d108902763bb71e85cd0fd382dc9357b59604ae03da6ae734d16890"
ROOT = Path("/opt/open-agent/temporal")
BINARY = Path("/opt/open-agent/runtime/temporal") / VERSION / "temporal"
UNIT = Path("/etc/systemd/system/open-agent-temporal.service")
MARKER = "# Managed by open_agent: Temporal demo only"
PORTS = (17233, 17234, 17235)


def command(*args, check=True, timeout=90):
    return subprocess.run(
        args, check=check, capture_output=True, text=True, timeout=timeout
    )


def main(archive: Path):
    if os.geteuid() != 0 or platform.machine() != "x86_64":
        raise RuntimeError(
            "Requires the authorized x86_64 Linux demo host administrator"
        )
    if hashlib.sha256(archive.read_bytes()).hexdigest() != SHA256:
        raise RuntimeError("Official archive checksum mismatch")
    if UNIT.exists() and MARKER not in UNIT.read_text():
        raise RuntimeError("Refusing to overwrite an unrelated service")
    active = (
        command("systemctl", "is-active", "open-agent-temporal", check=False).returncode
        == 0
    )
    if not active:
        for port in PORTS:
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", port))
    BINARY.parent.mkdir(parents=True, exist_ok=True)
    # Extract only these two ordinary files; no archive-supplied paths or links.
    with tarfile.open(archive, "r:gz") as package:
        for name in ("temporal", "LICENSE"):
            members = [
                member
                for member in package.getmembers()
                if member.name in {name, "./" + name}
            ]
            if (
                len(members) != 1
                or not members[0].isfile()
                or members[0].size > 160 * 1024 * 1024
            ):
                raise RuntimeError("Unexpected official archive contents")
            content = package.extractfile(members[0]).read()
            destination = BINARY.parent / name
            if destination.exists() and destination.read_bytes() != content:
                raise RuntimeError(
                    "Pinned runtime file already exists with a different digest"
                )
            if not destination.exists():
                destination.write_bytes(content)
            destination.chmod(0o755 if name == "temporal" else 0o644)
    try:
        account = pwd.getpwnam("open-agent-temporal")
    except KeyError:
        command(
            "useradd",
            "--system",
            "--no-create-home",
            "--shell",
            "/sbin/nologin",
            "open-agent-temporal",
        )
        account = pwd.getpwnam("open-agent-temporal")
    state = ROOT / "state"
    state.mkdir(parents=True, exist_ok=True)
    os.chown(state, account.pw_uid, account.pw_gid)
    state.chmod(0o700)
    unit = f"""{MARKER}
[Unit]
Description=Open Agent demo Temporal service
After=network.target
[Service]
Type=simple
User=open-agent-temporal
Group={account.pw_gid}
ExecStart={BINARY} --env-file /dev/null --disable-config-file --disable-config-env server start-dev --ip 127.0.0.1 --port 17233 --http-port 17234 --metrics-port 17235 --headless --ui-disable-news-fetch --db-filename {state}/temporal.db --log-level warn --log-format json
Restart=on-failure
RestartSec=5
TimeoutStopSec=30
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=yes
ReadWritePaths={state}
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
IPAddressDeny=any
IPAddressAllow=localhost
CapabilityBoundingSet=
MemoryMax=768M
CPUQuota=100%
TasksMax=128
UMask=0077
[Install]
WantedBy=multi-user.target
"""
    changed = not UNIT.exists() or UNIT.read_text() != unit
    UNIT.write_text(unit)
    UNIT.chmod(0o644)
    command("systemctl", "daemon-reload")
    command("systemctl", "enable", "open-agent-temporal")
    if changed and active:
        command("systemctl", "restart", "open-agent-temporal")
    else:
        command("systemctl", "start", "open-agent-temporal")
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        try:
            health = command(
                str(BINARY),
                "--env-file",
                "/dev/null",
                "--disable-config-file",
                "--disable-config-env",
                "--command-timeout",
                "3s",
                "--address",
                "127.0.0.1:17233",
                "operator",
                "cluster",
                "health",
                check=False,
                timeout=5,
            )
        except subprocess.TimeoutExpired:
            continue
        if health.returncode == 0:
            break
        time.sleep(1)
    else:
        command("systemctl", "stop", "open-agent-temporal")
        raise RuntimeError("Temporal demo did not become healthy")
    pid = command(
        "systemctl", "show", "-p", "MainPID", "--value", "open-agent-temporal"
    ).stdout.strip()
    sockets = command("ss", "-lntp").stdout.splitlines()
    listeners = [line for line in sockets if "pid=" + pid + "," in line]
    if not listeners or any(
        not line.split()[3].startswith(("127.0.0.1:", "[::1]:")) for line in listeners
    ):
        command("systemctl", "stop", "open-agent-temporal")
        raise RuntimeError("Temporal listener is not restricted to loopback")
    result = {
        "cli_version": VERSION,
        "archive_sha256": SHA256,
        "health": health.stdout.strip(),
        "persistent_state": str(state / "temporal.db"),
        "listeners": listeners,
        "demo_only": True,
    }
    ROOT.joinpath("verification.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, required=True)
    main(parser.parse_args().archive)
