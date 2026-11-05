"""Run one immutable deployment through the real DeepAgents engine.

Only a trusted activity supplies the lease, model configuration and sandbox factory.
User inputs cannot select plugins, transports, host commands or connection credentials.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable, Mapping
from contextlib import aclosing
from datetime import datetime, timezone
import ssl
from typing import Annotated, Any, Literal
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, Field

from common.otlp.log_trace.node_trace_log import NodeTraceLog
from common.otlp.trace.span import Span
from agent.api.schemas.agent_response import ToolExecution
from agent.api.schemas.llm_message import LLMMessage
from agent.domain.models.endpoint_policy import EndpointPolicy, PinnedModelTransport
from agent.domain.models.providers import create_provider_model
from agent.durable.control import AttemptLease, ControlPlaneClient
from agent.durable.permit import ExecutionPermit, PermitTransport
from agent.durable.runtime_config import RuntimeBundle, RuntimeReleaseSource
from agent.engine.deepagents_runner import DeepAgentsRunner
from agent.engine.observations import ExecutionObservations
from agent.platform_runtime.artifacts import ReleaseRef, load_release
from agent.platform_runtime.sandbox import OpenSandboxProvider, RunScope
from agent.platform_runtime.outputs import OUTPUT_ROOT, snapshot, read_snapshot, state_bytes


class HistoryMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["user", "assistant"]
    content: Annotated[str, Field(max_length=8192)]


class RunInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: Annotated[str, Field(min_length=1, max_length=32768)]
    history: Annotated[list[HistoryMessage], Field(max_length=32)] = Field(
        default_factory=list
    )


class ExecutionPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    engine: Literal["deepagents"] = "deepagents"
    instructions: Annotated[str, Field(max_length=32768)] = ""
    answerInstructions: Annotated[str, Field(max_length=16384)] = ""
    maxLoops: Annotated[int, Field(ge=1, le=30)] = 10
    temperature: Annotated[float, Field(ge=0, le=2)] = 0.2
    maxTokens: Annotated[int, Field(ge=1, le=16384)] = 4096


class RuntimeExecutor:
    def __init__(
        self,
        control: ControlPlaneClient,
        *,
        sandbox_factory: (
            Callable[[AttemptLease, ExecutionPermit], OpenSandboxProvider] | None
        ) = None,
        policy: EndpointPolicy | None = None,
        ca_files: Mapping[str, str] | None = None,
    ):
        self.control, self.sandbox_factory = control, sandbox_factory
        self.policy = policy or EndpointPolicy.from_env()
        self.ca_files = dict(ca_files or {})

    async def execute(
        self,
        lease: AttemptLease,
        bundle: RuntimeBundle,
        span: Span,
        trace: NodeTraceLog,
        *,
        heartbeat: Callable[[], None] = lambda: None,
    ) -> dict[str, int]:
        # Validation exceptions may include private input: sanitize before an activity
        # exception is ever serialized by Temporal's failure converter.
        try:
            user = RunInput.model_validate(bundle.input)
            plan = ExecutionPlan.model_validate(bundle.snapshot.get("execution", {}))
            self.policy.validate_url(bundle.model.endpoint)
            if (
                bundle.model.caRef is not None
                and bundle.model.caRef not in self.ca_files
            ):
                raise ValueError()
            if bundle.skills and (
                not bundle.model.supportsTools or self.sandbox_factory is None
            ):
                raise ValueError()
        except Exception:
            raise RuntimeError("execution_configuration_invalid") from None

        permit = ExecutionPermit(self.control, lease, bundle.deadline)
        observations = ExecutionObservations(lambda phase, step: self.control.observation(lease, phase, step))
        sequence = 0

        async def emit(kind: str, payload: dict[str, Any]):
            nonlocal sequence
            sequence += 1
            await self.control.append(
                lease, f"attempt-{lease.attempt}:{sequence}", kind, payload
            )

        async def run() -> dict[str, int]:
            attempt = None
            handle = None
            try:
                await observations.start("attempt", "agent", "DeepAgents", None)
                if bundle.skills:
                    releases = []
                    total = 0
                    source = RuntimeReleaseSource(self.control, lease)
                    for binding in bundle.skills:
                        await permit.check()
                        skill_step = await observations.custom("skill", "load_skill", summary={"releaseId": str(binding.releaseId), "manifestSha256": binding.manifestSha256})
                        release = await load_release(
                            source,
                            ReleaseRef(binding.releaseId, binding.manifestSha256),
                        )
                        total += sum(len(content) for content in release.contents)
                        if total > 20 * 1024 * 1024:
                            raise RuntimeError("skill_total_size_exceeded")
                        releases.append(release)
                        await observations.finish(skill_step, summary={"fileCount": str(len(release.contents))})
                    provider = self.sandbox_factory(lease, permit)
                    if (
                        not isinstance(provider, OpenSandboxProvider)
                        or provider.permission != permit.check
                    ):
                        raise RuntimeError("sandbox_permission_not_bound")
                    scope = RunScope(
                        lease.run.tenant,
                        lease.run.environment,
                        UUID(lease.run.run_id),
                        lease.attempt,
                        UUID(lease.lease_id),
                        bundle.deadline,
                    )
                    attempt = await provider.create(scope, releases)

                config = bundle.model
                context = ssl.create_default_context(
                    cafile=self.ca_files.get(config.caRef)
                )
                client = httpx.AsyncClient(
                    transport=PermitTransport(
                        permit, PinnedModelTransport(self.policy, verify=context)
                    ),
                    timeout=httpx.Timeout(
                        config.readTimeoutSeconds,
                        connect=config.connectTimeoutSeconds,
                        pool=config.connectTimeoutSeconds,
                    ),
                    trust_env=False,
                    follow_redirects=False,
                )
                handle = await create_provider_model(
                    model_name=config.modelName,
                    base_url=config.endpoint,
                    provider="openai",
                    api_key=config.credential.get_secret_value(),
                    http_client=client,
                    parameters={
                        "temperature": plan.temperature,
                        "max_tokens": plan.maxTokens,
                    },
                    timeout=config.readTimeoutSeconds,
                    max_retries=0,
                    stream_usage=config.supportsUsage,
                )
                runner = DeepAgentsRunner(
                    model_handle=handle,
                    plugins=[],
                    question=user.message,
                    chat_history=[
                        LLMMessage(role=message.role, content=message.content)
                        for message in user.history
                    ],
                    reasoning_instruction=plan.instructions,
                    answer_instruction=plan.answerInstructions,
                    max_loop=plan.maxLoops,
                )
                if attempt is not None:
                    runner.bind_attempt(attempt)
                runner.bind_observations(observations)
                archived: set[tuple[str,str]] = set()

                async def publish_output(descriptor: dict, content):
                    tool_step = observations.tool_steps.get(descriptor["step"])
                    output_step = await observations.custom("artifact", "publish_output", observations.parents.get(tool_step) or "attempt",
                        summary={"toolCallId": descriptor["step"]})
                    try:
                        await self.control.output(lease, descriptor, content)
                    except BaseException as error:
                        await observations.finish(output_step, "interrupted" if isinstance(error, asyncio.CancelledError) else "failed", summary={"errorType": "OutputPublicationFailed"})
                        raise
                    await observations.finish(output_step, summary={"fileCount": "1"})

                async def archive(step: str, state: dict | None = None):
                    await permit.check()
                    snapshots=[]
                    try:
                        if attempt is not None:
                            snapshots=await snapshot(attempt)
                            for row in snapshots:
                                key=(row["path"],row["sha256"])
                                if key not in archived:
                                    await publish_output({"path":row["path"],"size":row["size"],"sha256":row["sha256"],"step":step},read_snapshot(attempt,row))
                                    archived.add(key)
                        elif state:
                            candidates=[(path,value) for path,value in state.items() if path.startswith(OUTPUT_ROOT) and value is not None]
                            if len(candidates)>32:
                                raise RuntimeError("output_count_limit")
                            for path,value in candidates:
                                data=state_bytes(value)
                                sha=hashlib.sha256(data).hexdigest();key=(path,sha)
                                if key in archived:
                                    continue
                                async def chunks(data=data):
                                    for offset in range(0,len(data),65536):
                                        await permit.check()
                                        yield data[offset:offset+65536]
                                await publish_output({"path":path,"size":len(data),"sha256":sha,"step":step},chunks())
                                archived.add(key)
                    except Exception:
                        await emit("execution.notice",{"phase":"output_archive_failed"})
                        raise RuntimeError("output_archive_failed") from None
                    finally:
                        if attempt is not None and snapshots:
                            try:
                                await attempt.sandbox.files.delete_directories([snapshots[0]["snapshot"].rsplit("/",1)[0]])
                            except Exception:
                                pass # Destruction also removes private temporary snapshots.
                if bundle.model.supportsTools:
                    runner.bind_outputs(archive)
                await emit(
                    "execution.notice", {"phase": "executing", "engine": "deepagents"}
                )
                initial_trace = len(trace.trace)
                async with aclosing(runner.run(span, trace)) as responses:
                    async for response in responses:
                        permit.local_check()
                        if response.typ == "content" and isinstance(
                            response.content, str
                        ):
                            for offset in range(0, len(response.content), 2048):
                                await emit(
                                    "message.delta",
                                    {"text": response.content[offset : offset + 2048]},
                                )
                        elif response.typ == "tool_result" and isinstance(
                            response.content, ToolExecution
                        ):
                            # Tool arguments and outputs can contain private business data.
                            await emit(
                                "tool.finished", {"name": response.content.action[:120], "step":runner.tool_step}
                            )
                nodes = trace.trace[initial_trace:]
                usage = {
                    name: sum(getattr(node.data.usage, name) for node in nodes)
                    for name in ("prompt_tokens", "completion_tokens", "total_tokens")
                }
                await emit("usage", usage)
                await observations.finish("attempt")
                return usage
            except BaseException as error:
                try:
                    async with asyncio.timeout(5):
                        await observations.finish("attempt", "interrupted" if isinstance(error, asyncio.CancelledError) else "failed", summary={"errorType": type(error).__name__[:64]})
                except Exception:
                    pass
                raise
            finally:
                # Cancellation can finish existing steps with the same owned lease. A
                # killed process leaves persisted starts explicitly incomplete instead.
                try:
                    async with asyncio.timeout(8):
                        await observations.interrupt_open()
                except Exception:
                    pass
                if handle is not None:
                    await asyncio.shield(handle.aclose())
                if attempt is not None:
                    # This requests cleanup; only an independent physical check may release quota.
                    await asyncio.shield(attempt.close())

        remaining = (bundle.deadline - datetime.now(timezone.utc)).total_seconds()
        async with asyncio.timeout(
            max(0, min(remaining, bundle.model.totalTimeoutSeconds))
        ):
            return await permit.run(run, heartbeat=heartbeat)
