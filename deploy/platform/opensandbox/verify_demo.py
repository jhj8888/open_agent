"""Destructive tests only for sandboxes created by this verification run.

Uses real official SDK calls and the actual platform adapter. Host Docker inspection
is a test-harness privilege; it must never be given to a tenant worker or Skill.
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import logging
from pathlib import Path
import shlex
import sqlite3
import subprocess
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from agent.platform_runtime.artifacts import ReleaseRef, load_release
from agent.platform_runtime.backend import OpenSandboxBackend
from agent.platform_runtime.sandbox import OpenSandboxProvider, RunScope, SandboxFailure
from agent.platform_runtime.transport import sandbox_connection
from opensandbox.manager import SandboxManager
from opensandbox.sandbox import Sandbox
from deepagents import create_deep_agent
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, ToolMessage
from pydantic import Field

ROOT = Path("/opt/open-agent/sandbox")
logging.getLogger("opensandbox").setLevel(logging.CRITICAL)
logging.getLogger("httpx").setLevel(logging.WARNING)


class SyntheticModel(FakeMessagesListChatModel):
    """Only the model is synthetic; Skills discovery, graph and sandbox are real."""

    prompts: list = Field(default_factory=list)

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.prompts.append(messages)
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

    def get_num_tokens_from_messages(self, messages, tools=None):
        return 100


async def command(*args, check=True):
    def run():
        value = subprocess.run(args, capture_output=True, text=True, timeout=30)
        if check and value.returncode:
            raise RuntimeError(
                f"Host validation failed: {args[:2]}: {value.stderr[-600:]}"
            )
        return value

    return await asyncio.to_thread(run)


class Journal:
    """Durable verification journal; production quota/fencing is a separate implementation."""

    def __init__(self):
        self.path = ROOT / "verification-leases.sqlite"
        with sqlite3.connect(self.path) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS leases (id TEXT PRIMARY KEY, tenant TEXT, run TEXT, sandbox TEXT, state TEXT)"
            )

    async def write(self, scope, sandbox_id, state):
        def update():
            with sqlite3.connect(self.path) as db:
                db.execute(
                    "INSERT INTO leases VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET state=excluded.state",
                    (
                        str(scope.lease_id),
                        scope.tenant_id,
                        str(scope.run_id),
                        sandbox_id,
                        state,
                    ),
                )

        await asyncio.to_thread(update)

    async def created(self, scope, sandbox_id):
        await self.write(scope, sandbox_id, "created")

    async def cleanup_pending(self, scope, sandbox_id):
        await self.write(scope, sandbox_id, "cleanup_pending")

    async def released(self, scope, sandbox_id):
        await self.write(scope, sandbox_id, "released")


class Admission:
    def __init__(self, image):
        self.image = image
        self.addresses = {}

    async def before_create(self, scope, profile, image):
        assert (
            scope.environment == "test"
            and profile == "gvisor-demo"
            and image == self.image
        )
        config = json.loads(Path("/etc/docker/daemon.json").read_text())
        runtime = config["runtimes"]["open-agent-runsc"]
        assert runtime["path"] == "/opt/open-agent/runtime/gvisor/20261102.0/runsc"
        assert runtime["runtimeArgs"] == ["--platform=systrap", "--overlay2=all:memory"]
        networks = json.loads(
            (await command("docker", "network", "inspect", "open-agent-sandbox")).stdout
        )
        network = networks[0]
        assert network["Internal"] and not network["EnableIPv6"]
        assert network["Options"]["com.docker.network.bridge.enable_icc"] == "false"
        nameservers = [
            line.split()[1]
            for line in Path("/etc/resolv.conf").read_text().splitlines()
            if line.startswith("nameserver ")
        ]
        assert nameservers and not any(
            ipaddress.ip_address(ip).is_loopback for ip in nameservers
        )
        for direction in ["-i", "-o"]:
            await command(
                "iptables",
                "-w",
                "5",
                "-C",
                "DOCKER-USER",
                direction,
                "oa-sandbox0",
                "-m",
                "comment",
                "--comment",
                "open-agent-sandbox-forward-deny",
                "-j",
                "DROP",
            )
        await command(
            "iptables",
            "-w",
            "5",
            "-C",
            "INPUT",
            "-i",
            "oa-sandbox0",
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
        )
        await command(
            "iptables",
            "-w",
            "5",
            "-C",
            "INPUT",
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
        )

    async def after_create(self, scope, profile, sandbox_id):
        UUID(sandbox_id)
        container = json.loads(
            (await command("docker", "inspect", "sandbox-" + sandbox_id)).stdout
        )[0]
        host = container["HostConfig"]
        assert all(
            container["Config"]["Labels"].get(k) == v
            for k, v in scope.metadata().items()
        )
        assert host["Runtime"] == "open-agent-runsc" and not host["Privileged"]
        assert host["NetworkMode"] == "open-agent-sandbox"
        assert host["PidMode"] != "host" and host["IpcMode"] != "host"
        assert not host["Binds"] and not host["Devices"] and not host["CapAdd"]
        assert (
            0 < host["Memory"] <= 512 * 1024 * 1024
            and 0 < host["NanoCpus"] <= 1_000_000_000
        )
        assert 0 < host["PidsLimit"] <= 256 and "no-new-privileges" in " ".join(
            host["SecurityOpt"]
        )
        assert {"NET_ADMIN", "NET_RAW", "SYS_ADMIN", "SYS_PTRACE"}.issubset(
            set(host["CapDrop"])
        )
        assert not container["Mounts"], "Sandbox has an unexpected host/volume mount"
        self.addresses[sandbox_id] = container["NetworkSettings"]["Networks"][
            "open-agent-sandbox"
        ]["IPAddress"]
        # The demo uses a pinned official Python image. The production runtime image
        # must pre-create this account during its reviewed image build.
        await command(
            "docker",
            "exec",
            "--user",
            "0:0",
            "sandbox-" + sandbox_id,
            "/bin/sh",
            "-c",
            'set -eu; useradd --uid 1000 --user-group --no-create-home --home-dir /workspace --shell /usr/sbin/nologin agent; test "$(id -u agent)" = 1000; test "$(id -g agent)" = 1000',
        )


class Source:
    def __init__(self, image):
        self.contents = [
            b"---\nname: isolation-check\ndescription: Synthetic sandbox verification\n---\nRun scripts/check.py to verify the runtime.\n",
            b'import os\nprint("uid=" + str(os.getuid()))\n',
        ]
        manifest = {
            "schemaVersion": 1,
            "name": "isolation-check",
            "version": "verification-1",
            "runtimeImage": image,
            "dependencyPolicy": "preinstalled-image-only",
            "files": [
                {
                    "path": path,
                    "size": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "dependencyLock": False,
                }
                for path, data in zip(["SKILL.md", "scripts/check.py"], self.contents)
            ],
        }
        self.raw = json.dumps(manifest, separators=(",", ":")).encode()

    async def manifest(self, reference):
        return self.raw

    async def file(self, reference, index, max_bytes):
        assert len(self.contents[index]) <= max_bytes
        return self.contents[index]


def scope(tenant):
    return RunScope(
        tenant,
        "test",
        uuid4(),
        1,
        uuid4(),
        datetime.now(timezone.utc) + timedelta(minutes=5),
    )


async def main():
    deployment = json.loads((ROOT / "deployment.json").read_text())
    connection = sandbox_connection(
        deployment["endpoint"], (ROOT / "api-key").read_text().strip()
    )
    journal = Journal()
    admission = Admission(deployment["runtime_image"])

    async def diagnostic_create(*args, **kwargs):
        try:
            return await Sandbox.create(*args, **kwargs)
        except Exception as error:
            detail = getattr(error, "error", None)
            message = str(getattr(detail, "message", type(error).__name__))
            for value in [connection.api_key, *kwargs.get("env", {}).values()]:
                if value:
                    message = message.replace(value, "[redacted]")
            print("SDK create error: " + message[:1200], flush=True)
            raise

    provider = OpenSandboxProvider(
        connection,
        journal,
        admission,
        profile="gvisor-demo",
        memory="384Mi",
        factory=diagnostic_create,
    )
    source = Source(deployment["runtime_image"])
    release = await load_release(
        source, ReleaseRef(uuid4(), hashlib.sha256(source.raw).hexdigest())
    )
    leases = []
    passed = []

    def success(name):
        passed.append(name)
        print("PASS " + name, flush=True)

    try:
        a = await provider.create(scope("verification-a"), [release])
        leases.append(a)
        b = await provider.create(scope("verification-b"), [release])
        leases.append(b)
        assert a.sandbox.id != b.sandbox.id
        success(
            "real gVisor runtime, resource limits and per-attempt metadata admitted"
        )
        result = await a.execute("python3 /skills/isolation-check/scripts/check.py")
        assert result.exit_code == 0 and result.stdout.strip() == "uid=1000", repr(
            result
        )
        success("fixed release bytes execute as UID 1000")
        result = await a.execute(
            "echo tenant-a > /workspace/private.txt; test ! -e /var/run/docker.sock; test ! -e /var/run/secrets/kubernetes.io/serviceaccount/token"
        )
        assert result.exit_code == 0
        assert (await b.execute("test ! -e /workspace/private.txt")).exit_code == 0
        assert (
            await a.execute("echo changed > /skills/isolation-check/SKILL.md")
        ).exit_code != 0
        success(
            "tenant files isolated, Skill read-only, no Docker socket or service-account token"
        )
        secret_check = """import os
names=[k for k in os.environ if 'TOKEN' in k or 'API_KEY' in k or 'SECRET' in k]
assert not names, 'credential variable names: ' + ','.join(names)
try:
    with open('/proc/1/environ','rb') as f:
        f.read(1)
except PermissionError:
    pass
else:
    raise AssertionError('root process environment can be opened')
print('clean')"""
        result = await a.execute("python3 -c " + shlex.quote(secret_check))
        assert result.exit_code == 0, result.stderr[-1000:]
        success(
            "sandbox credentials absent from child environment and root process inaccessible"
        )
        denied_api = """import urllib.request, urllib.error
request=urllib.request.Request('http://127.0.0.1:44772/command', data=b'{"command":"id"}', headers={'Content-Type':'application/json'})
try:
    urllib.request.urlopen(request,timeout=2)
except urllib.error.HTTPError as e:
    assert e.code in (401,403), str(e.code)
else:
    raise AssertionError('unauthenticated execd accepted command')"""
        result = await a.execute("python3 -c " + shlex.quote(denied_api))
        assert result.exit_code == 0, result.stderr[-1000:]
        success("untrusted code cannot call the local execd control API")
        peer = admission.addresses[b.sandbox.id]
        network_check = """import socket, struct
targets = [('1.1.1.1', 443), ('169.254.169.254', 80), ('100.100.100.200', 80), ('172.30.241.1', 22), (PEER, 44772)]
for host, port in targets:
    s=socket.socket(); s.settimeout(1)
    try:
        s.connect((host, port))
    except OSError:
        pass
    else:
        raise AssertionError('network escape: ' + host)
    finally:
        s.close()
packet=struct.pack('!HHHHHH', 1234, 0x100, 1, 0, 0, 0)+b'\\x07example\\x03com\\x00'+struct.pack('!HH',1,1)
for host in ['127.0.0.11', '100.100.2.136', '1.1.1.1']:
    s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); s.settimeout(1)
    try:
        s.sendto(packet,(host,53)); data=s.recv(4096)
        assert len(data)>=12 and data[3]&15 in (2,3,5), 'DNS escape'
    except OSError:
        pass
    finally:
        s.close()
print('network denied')""".replace(
            "PEER", repr(peer)
        )
        result = await a.execute("python3 -c " + shlex.quote(network_check), timeout=20)
        assert result.exit_code == 0, "network or DNS isolation failed"
        success("public IP, metadata, host, peer sandbox and direct DNS denied")
        backend = OpenSandboxBackend(a)
        uploaded = await backend.aupload_files(
            [("/workspace/report.txt", b"bounded artifact")]
        )
        downloaded = await backend.adownload_files(["/workspace/report.txt"])
        assert (
            uploaded[0].error is None and downloaded[0].content == b"bounded artifact"
        )
        await a.execute("ln -s /etc/passwd /workspace/escape")
        assert (await backend.adownload_files(["/workspace/escape"]))[0].error
        success(
            "artifact round trip and symlink rejection through native DeepAgents backend"
        )
        model = SyntheticModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "execute",
                            "id": "verify-execute",
                            "type": "tool_call",
                            "args": {
                                "command": "python3 /skills/isolation-check/scripts/check.py"
                            },
                        }
                    ],
                ),
                AIMessage(content="verified"),
            ]
        )
        graph = create_deep_agent(model=model, backend=backend, skills=["/skills/"])
        state = await graph.ainvoke(
            {
                "messages": [
                    {"role": "user", "content": "Run the isolation-check Skill."}
                ]
            },
            config={"recursion_limit": 12},
        )
        assert any(
            isinstance(message, ToolMessage) and "uid=1000" in str(message.content)
            for message in state["messages"]
        )
        assert any(
            "isolation-check" in str(messages[0].content) for messages in model.prompts
        )
        success(
            "real DeepAgents graph discovers SKILL.md and executes via the sandbox backend (synthetic model)"
        )
        result = await a.execute("python3 -c 'print(\"x\" * 200000)'")
        assert result.truncated and len(result.stdout.encode()) <= 65536
        success("streaming output capped at 64 KiB")
        limits = "import resource; assert resource.getrlimit(resource.RLIMIT_NPROC)==(128,128); assert resource.getrlimit(resource.RLIMIT_FSIZE)==(67108864,67108864)"
        result = await a.execute("python3 -c " + shlex.quote(limits))
        assert result.exit_code == 0, result.stderr[-1000:]
        success("unprivileged hard PID and file-size limits enforced")
        assert await provider.find_attempt_sandboxes(a.scope) == [a.sandbox.id]
        success("official SDK recovery finds exact trusted attempt metadata")
        pending = asyncio.create_task(b.execute("sleep 30"))
        await asyncio.sleep(1)
        pending.cancel()
        try:
            await pending
        except asyncio.CancelledError:
            pass
        await b.close()
        assert not await provider.find_attempt_sandboxes(b.scope)
        success("cancellation destroys sandbox and repeated close is idempotent")
        await a.close()
        assert not await provider.find_attempt_sandboxes(a.scope)
        with sqlite3.connect(journal.path) as db:
            for lease in leases:
                assert (
                    db.execute(
                        "SELECT state FROM leases WHERE id=?",
                        (str(lease.scope.lease_id),),
                    ).fetchone()[0]
                    == "released"
                )
        success("destroyed leases persisted as released")
    finally:
        for lease in leases:
            await lease.close()
        await connection.transport.aclose()
        (ROOT / "verification-result.json").write_text(
            json.dumps(
                {
                    "passed": passed,
                    "deployment": deployment,
                    "utc": datetime.now(timezone.utc).isoformat(),
                },
                indent=2,
            )
        )
    print(f"{len(passed)} real sandbox checks passed", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
