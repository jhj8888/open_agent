"""Authorized demo integration: real model, DeepAgents and gVisor; synthetic admission.

The model credential arrives on stdin and is never written to the sandbox or report.
Host inspection is a verification privilege, not a tenant-worker permission.
"""

import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import sys
from uuid import UUID, uuid4

os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"
from common.otlp import sid
from common.otlp.log_trace.node_trace_log import NodeTraceLog
from common.otlp.trace.span import Span
from agent.domain.models.endpoint_policy import EndpointPolicy, parse_endpoint
from agent.durable.contracts import RunRef
from agent.durable.control import AttemptLease
from agent.durable.executor import RuntimeExecutor
from agent.durable.runtime_config import RuntimeBundle
from agent.platform_runtime.sandbox import OpenSandboxProvider, RunScope
from agent.platform_runtime.transport import sandbox_connection
from opensandbox.manager import SandboxManager

sys.path.insert(0, "/opt/open-agent/sandbox")
from verify_demo import Admission, Journal, Source, command

ROOT = Path("/opt/open-agent/agent-worker")
logging.getLogger("opensandbox").setLevel(logging.CRITICAL)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("openai").setLevel(logging.WARNING)


class SyntheticAdmission:
    def __init__(self, source, release):
        self.source, self.release = source, release
        self.events = []
        self.permit_checks = 0

    async def permit(self, lease):
        self.permit_checks += 1
        return True

    async def heartbeat(self, lease):
        return True

    async def append(self, lease, key, kind, payload):
        self.events.append((key, kind, payload))

    async def artifact(self, lease, release_id, index, maximum):
        assert release_id == str(self.release)
        data = self.source.raw if index == -1 else self.source.contents[index]
        assert len(data) <= maximum
        return data


async def main(config):
    deployment = json.loads(Path("/opt/open-agent/sandbox/deployment.json").read_text())
    connection = sandbox_connection(
        deployment["endpoint"],
        Path("/opt/open-agent/sandbox/api-key").read_text().strip(),
    )
    source = Source(deployment["runtime_image"])
    release_id = uuid4()
    control = SyntheticAdmission(source, release_id)
    run = RunRef("executor-skill-verification", "test", str(uuid4()))
    lease = AttemptLease(run, 1, str(uuid4()), 1)
    deadline = datetime.now(timezone.utc) + timedelta(minutes=5)
    scope = RunScope(
        run.tenant, run.environment, UUID(run.run_id), 1, UUID(lease.lease_id), deadline
    )
    attempts = []
    executions = []

    class RecordingProvider(OpenSandboxProvider):
        async def create(self, scope, releases):
            attempt = await super().create(scope, releases)
            attempts.append(attempt)
            execute = attempt.execute

            async def record(text, timeout=None):
                result = await execute(text, timeout)
                executions.append(
                    {
                        "skill_script": bool(
                            re.search(
                                r"python3?\s+/skills/isolation-check/scripts/check\.py",
                                text,
                            )
                        ),
                        "uid_1000": result.stdout.strip() == "uid=1000",
                        "exit_code": result.exit_code,
                    }
                )
                return result

            attempt.execute = record
            return attempt

    def provider(bound_lease, permit):
        assert bound_lease == lease
        return RecordingProvider(
            connection,
            Journal(),
            Admission(deployment["runtime_image"]),
            profile="gvisor-demo",
            memory="384Mi",
            permission=permit.check,
        )

    endpoint = config["base_url"]
    origin = parse_endpoint(endpoint)[1]
    assert origin == "https://api.deepseek.com:443"
    policy = EndpointPolicy(frozenset({origin}), (), public_origins=frozenset({origin}))
    bundle = RuntimeBundle.model_validate(
        {
            "input": {
                "message": "请使用 isolation-check Skill，实际运行 python3 /skills/isolation-check/scripts/check.py，并在最终回答中原样给出它输出的 uid。不要猜测结果。"
            },
            "snapshot": {
                "execution": {
                    "instructions": "你正在执行平台联调。先按 Skill 说明执行脚本，然后简洁报告实际结果。",
                    "maxLoops": 6,
                    "maxTokens": 2048,
                    "temperature": 0,
                }
            },
            "deadline": deadline,
            "skills": [
                {
                    "releaseId": str(release_id),
                    "manifestSha256": hashlib.sha256(source.raw).hexdigest(),
                }
            ],
            "model": {
                "endpoint": endpoint,
                "modelName": config["model"],
                "credential": config.pop("api_key"),
                "version": 1,
                "supportsTools": True,
                "supportsUsage": True,
                "connectTimeoutSeconds": 10,
                "readTimeoutSeconds": 120,
                "totalTimeoutSeconds": 240,
                "caRef": None,
            },
        }
    )
    sid.init_sid(
        sid.SidInfo(
            sub="oa", location="demo", index=1, local_ip="127.0.0.1", local_port="0000"
        )
    )
    span = Span(app_id="executor-skill-verification", uid="synthetic")
    span.sid = run.run_id
    trace = NodeTraceLog(
        service_id="durable-executor-verification", sid=run.run_id, sub="agent"
    )
    try:
        usage = await RuntimeExecutor(
            control, policy=policy, sandbox_factory=provider
        ).execute(lease, bundle, span, trace)
        assert any(
            item["skill_script"] and item["uid_1000"] and item["exit_code"] == 0
            for item in executions
        )
        answer = "".join(
            payload["text"]
            for _, kind, payload in control.events
            if kind == "message.delta"
        )
        assert "1000" in answer, "Model did not include the actual script result"
        assert len(attempts) == 1
        for attempt in attempts:
            inspected = await command(
                "docker", "inspect", "sandbox-" + attempt.sandbox.id, check=False
            )
            assert (
                inspected.returncode != 0
            ), "Completed sandbox still exists in the physical runtime"
        report = {
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "scope": "real model + real DeepAgents + real OpenSandbox/gVisor; synthetic admission and SQLite verification journal",
            "requested_model": config["model"],
            "skill_executed_uid_1000": True,
            "model_reported_actual_uid": True,
            "physical_sandbox_deleted": True,
            "usage": usage,
            "permit_checks": control.permit_checks,
            "tool_events": sum(
                kind == "tool.finished" for _, kind, _ in control.events
            ),
            "versions": json.loads((ROOT / "deployment.json").read_text())["versions"],
            "not_verified": [
                "durable database admission/quota",
                "production execution/reconciliation activities",
                "public HTTP/chat interface",
                "production Kata/K8S",
                "full offline platform",
            ],
        }
        (ROOT / "verification-model-skill.json").write_text(
            json.dumps(report, indent=2) + "\n"
        )
        print(json.dumps(report, indent=2))
    finally:
        for attempt in attempts:
            await attempt.close()
        # Only remove this verification run's exact scoped resources, including an
        # ambiguous creation whose response did not reach the worker.
        recovery = OpenSandboxProvider(
            connection,
            Journal(),
            Admission(deployment["runtime_image"]),
            profile="gvisor-demo",
        )
        manager = await SandboxManager.create(connection_config=connection)
        try:
            for sandbox_id in await recovery.find_attempt_sandboxes(scope):
                await manager.kill_sandbox(sandbox_id)
        finally:
            await manager.close()
            await connection.transport.aclose()


if __name__ == "__main__":
    try:
        asyncio.run(main(json.loads(sys.stdin.read())))
    except Exception as error:
        print("Model/Skill integration failed:", type(error).__name__, flush=True)
        raise SystemExit(1)
