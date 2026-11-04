"""Trusted single-host executor supervision through the official systemd CLI.

This module runs only in the host supervisor, never in an executor or sandbox.
The caller commits the one-shot launch authorization BEFORE calling launch and
persists the observed InvocationID BEFORE requesting stop. It never retries launch.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import re
from uuid import UUID


class HostProcessError(RuntimeError):
    """Fixed codes only: subprocess output can contain private configuration."""


def canonical_uuid(value: str) -> str:
    try:
        if str(UUID(value)) != value:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise HostProcessError("HOST_IDENTIFIER_INVALID") from None
    return value


@dataclass(frozen=True)
class ProcessBinding:
    lease_id: str
    boot_id: str

    def __post_init__(self):
        canonical_uuid(self.lease_id)
        canonical_uuid(self.boot_id)

    @property
    def unit(self) -> str:
        return "oa-exec-" + self.lease_id.replace("-", "") + ".service"

    @property
    def cgroup(self) -> str:
        return "/openagent.slice/" + self.unit


@dataclass(frozen=True)
class WorkerProgram:
    python: Path
    source: Path
    credentials: Path
    attempt_file: Path

    def __post_init__(self):
        for value in (self.python, self.source, self.credentials, self.attempt_file):
            # Host configuration only; no systemd specifiers, environment expansion or shell syntax.
            if not re.fullmatch(r"/opt/open-agent/[A-Za-z0-9_./-]+", str(value)) or ".." in value.parts:
                raise HostProcessError("HOST_PROGRAM_INVALID")


class SystemdProcesses:
    USER = "open-agent-worker"
    PROPERTIES = (
        "Id", "LoadState", "ActiveState", "SubState", "MainPID", "ControlPID",
        "ControlGroup", "InvocationID", "Transient", "KillMode", "User",
        "RemainAfterExit", "Restart", "ExecMainCode", "ExecMainStatus",
    )

    def __init__(self):
        if os.name != "posix" or os.geteuid() != 0 or not Path("/sys/fs/cgroup/cgroup.controllers").is_file():
            raise HostProcessError("HOST_SUPERVISION_UNAVAILABLE")

    @staticmethod
    def boot_id() -> str:
        return canonical_uuid(Path("/proc/sys/kernel/random/boot_id").read_text().strip())

    async def _command(self, *args: str, timeout: int = 20) -> tuple[int, str]:
        process = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C", "SYSTEMD_PAGER": "cat"},
        )
        try:
            async with asyncio.timeout(timeout):
                output, _ = await process.communicate()
        except (TimeoutError, asyncio.CancelledError) as error:
            if process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
            await process.wait()
            # Killing the CLI does not prove its systemd job stopped. Keep the DB binding.
            if isinstance(error, asyncio.CancelledError):
                raise
            raise HostProcessError("HOST_OPERATION_UNCONFIRMED") from None
        if len(output) > 65536:
            raise HostProcessError("HOST_RESPONSE_INVALID")
        return process.returncode, output.decode("utf-8", errors="strict")

    async def inspect(self, binding: ProcessBinding) -> dict[str, str] | None:
        status, output = await self._command(
            "systemctl", "show", "--no-pager", "--property=" + ",".join(self.PROPERTIES), binding.unit
        )
        row = dict(line.split("=", 1) for line in output.splitlines() if "=" in line)
        if status in {0, 4} and row.get("LoadState") == "not-found":
            return None
        if status or set(row) != set(self.PROPERTIES):
            raise HostProcessError("HOST_UNIT_UNAVAILABLE")
        if (
            row["Id"] != binding.unit or row["Transient"] != "yes" or row["User"] != self.USER
            or row["KillMode"] != "control-group" or row["Restart"] != "no"
            or row["RemainAfterExit"] != "yes" or row["ControlGroup"] not in {"", binding.cgroup}
            or not re.fullmatch(r"[a-f0-9]{32}", row["InvocationID"])
            or not row["MainPID"].isdigit() or not row["ControlPID"].isdigit()
        ):
            raise HostProcessError("HOST_UNIT_IDENTITY_MISMATCH")
        return row

    async def launch(self, binding: ProcessBinding, program: WorkerProgram) -> dict[str, str]:
        if binding.boot_id != self.boot_id() or await self.inspect(binding) is not None:
            raise HostProcessError("HOST_LAUNCH_CONFLICT")
        properties = [
            "Type=exec", "RemainAfterExit=yes", "Restart=no", "KillMode=control-group",
            "SendSIGKILL=yes", "TimeoutStopSec=10s", "TimeoutStartSec=20s", "RuntimeMaxSec=3610s",
            "User=" + self.USER, "Group=" + self.USER, "Slice=openagent.slice",
            "NoNewPrivileges=yes", "CapabilityBoundingSet=", "AmbientCapabilities=",
            "PrivateTmp=yes", "PrivateDevices=yes", "ProtectSystem=strict", "ProtectHome=yes",
            "ProtectKernelTunables=yes", "ProtectKernelModules=yes", "ProtectKernelLogs=yes",
            "ProtectControlGroups=yes", "RestrictSUIDSGID=yes", "RestrictRealtime=yes",
            "RestrictNamespaces=yes", "LockPersonality=yes", "ProtectProc=invisible",
            "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6", "UMask=0077",
            "InaccessiblePaths=-/run/docker.sock -/run/dbus/system_bus_socket /opt/open-agent/control",
            "MemoryMax=512M", "MemorySwapMax=0", "CPUQuota=100%", "TasksMax=128",
            "StandardOutput=journal", "StandardError=journal",
            "LoadCredential=worker.json:" + str(program.credentials),
            "Environment=PYTHONPATH=" + str(program.source / "core") + ":" + str(program.source / "core/common"),
            "Environment=PYTHONDONTWRITEBYTECODE=1 LANGSMITH_TRACING=false LANGCHAIN_TRACING_V2=false",
        ]
        command = ["systemd-run", "--quiet", "--no-ask-password", "--expand-environment=no", "--unit=" + binding.unit]
        command.extend("--property=" + value for value in properties)
        command.extend([str(program.python), "-m", "agent.durable.child", str(program.attempt_file)])
        status, _ = await self._command(*command, timeout=25)
        if status:
            raise HostProcessError("HOST_LAUNCH_UNCONFIRMED")
        row = await self.inspect(binding)
        if row is None or binding.boot_id != self.boot_id():
            raise HostProcessError("HOST_LAUNCH_UNCONFIRMED")
        return row

    @staticmethod
    def _empty_cgroup(binding: ProcessBinding) -> dict[str, object]:
        group = Path("/sys/fs/cgroup" + binding.cgroup)
        try:
            events = dict(line.split() for line in (group / "cgroup.events").read_text().splitlines())
            # populated includes descendants; checking only the top cgroup.procs is insufficient.
            if events.get("populated") != "0" or (group / "cgroup.procs").read_text().strip():
                raise HostProcessError("HOST_PROCESS_STILL_RUNNING")
            return {"path": binding.cgroup, "exists": True, "populated": 0}
        except FileNotFoundError:
            if group.exists():
                raise HostProcessError("HOST_CGROUP_UNCONFIRMED") from None
            return {"path": binding.cgroup, "exists": False, "populated": 0}

    async def stop(self, binding: ProcessBinding, invocation_id: str | None, *, never_launched: bool = False) -> dict[str, object]:
        if invocation_id is not None and not re.fullmatch(r"[a-f0-9]{32}", invocation_id):
            raise HostProcessError("HOST_IDENTIFIER_INVALID")
        current_boot = self.boot_id()
        before = await self.inspect(binding)
        if current_boot != binding.boot_id:
            if before is not None:
                raise HostProcessError("HOST_UNIT_IDENTITY_MISMATCH")
            kind = "host_reboot"
        elif never_launched:
            if before is not None or invocation_id is not None:
                raise HostProcessError("HOST_LAUNCH_CONFLICT")
            kind = "never_launched"
        else:
            if invocation_id is None:
                raise HostProcessError("HOST_LAUNCH_UNCONFIRMED")
            if before is not None:
                if before["InvocationID"] != invocation_id:
                    raise HostProcessError("HOST_UNIT_IDENTITY_MISMATCH")
                status, _ = await self._command("systemctl", "stop", "--no-ask-password", binding.unit)
                if status:
                    raise HostProcessError("HOST_STOP_UNCONFIRMED")
            kind = "stopped_unit"
        after = await self.inspect(binding)
        if after is not None and (
            after["InvocationID"] != invocation_id or after["ActiveState"] not in {"inactive", "failed"}
            or after["MainPID"] != "0" or after["ControlPID"] != "0"
        ):
            raise HostProcessError("HOST_PROCESS_STILL_RUNNING")
        cgroup = self._empty_cgroup(binding)
        if current_boot != self.boot_id():
            raise HostProcessError("HOST_BOOT_CHANGED")
        return {"kind": kind, "bootId": current_boot, "unitName": binding.unit, "invocationId": invocation_id,
                "workerStopped": True, "cgroupEmpty": True, "before": before, "after": after,
                "cgroup": cgroup, "observedAt": datetime.now(timezone.utc).isoformat()}
