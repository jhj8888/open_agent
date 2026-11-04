"""Non-root, single-attempt entry point. Tenant scripts execute only in OpenSandbox.

Launched by systemd with a lease-only argument file and LoadCredential. No model
credential is persisted in either file; the current lease gates runtime retrieval.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import os
from pathlib import Path
import signal
import sys
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from agent.durable.control import ControlPlaneClient, ControlPlaneError
from agent.durable.contracts import TERMINAL
from agent.durable.supervision import SupervisedLease


def failure_location(error: BaseException) -> dict:
    """Source locations only; exception text, locals and source lines may contain credentials."""
    frames = []
    trace = error.__traceback__
    while trace is not None:
        code = trace.tb_frame.f_code
        frames.append({'file': Path(code.co_filename).name, 'function': code.co_name, 'line': trace.tb_lineno})
        trace = trace.tb_next
    result = {'error_type': type(error).__name__, 'frames': frames[-8:]}
    if isinstance(error, ControlPlaneError):
        result['control_category'] = error.category
    return result


class SandboxSettings(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    endpoint: str
    credential: SecretStr = Field(repr=False)
    profile: Literal['gvisor-demo', 'kata']
    admission_socket: Literal['/run/open-agent/supervisor.sock']
    admission_capability: SecretStr = Field(repr=False)
    ca_file: str | None = None


class ExecutorSettings(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    lease_id: str
    control_endpoint: str
    worker_id: str
    worker_key: SecretStr = Field(repr=False)
    control_ca_file: str | None = None
    model_origins: Annotated[list[str], Field(max_length=100)]
    model_public_origins: Annotated[list[str], Field(max_length=100)] = Field(default_factory=list)
    model_networks: Annotated[list[str], Field(max_length=100)] = Field(default_factory=list)
    model_denied_addresses: Annotated[list[str], Field(max_length=100)] = Field(default_factory=list)
    model_ca_files: dict[str, str] = Field(default_factory=dict)
    sandbox: SandboxSettings | None = Field(default=None, repr=False)


def document(path: Path, maximum: int = 65536):
    if path.is_symlink():
        raise RuntimeError('executor_configuration_invalid')
    with path.open('rb') as stream:
        raw = stream.read(maximum + 1)
    if len(raw) > maximum:
        raise RuntimeError('executor_configuration_invalid')
    return json.loads(raw)


async def execute_attempt(settings: ExecutorSettings, record: SupervisedLease) -> str:
    if settings.lease_id != record.leaseId or settings.worker_id != record.workerId:
        raise RuntimeError('executor_configuration_invalid')
    # Only this isolated trusted executor process is affected by logging suppression.
    # Raw framework exceptions and validation input are never serialized to Temporal/journald.
    logging.disable(logging.CRITICAL)
    from loguru import logger
    logger.remove()
    from common.otlp import sid
    from common.otlp.log_trace.node_trace_log import NodeTraceLog
    from common.otlp.trace.span import Span
    from agent.domain.models.endpoint_policy import EndpointPolicy, parse_endpoint
    from agent.durable.executor import RuntimeExecutor
    from agent.durable.host_admission import HostAdmissionClient
    from agent.durable.sandbox_journal import DurableSandboxJournal
    from agent.platform_runtime.sandbox import OpenSandboxProvider
    from agent.platform_runtime.transport import sandbox_connection

    origins = frozenset(parse_endpoint(value)[1] for value in settings.model_origins)
    public = frozenset(parse_endpoint(value)[1] for value in settings.model_public_origins)
    if not public.issubset(origins):
        raise RuntimeError('executor_configuration_invalid')
    policy = EndpointPolicy(origins, tuple(ipaddress.ip_network(value, strict=True) for value in settings.model_networks),
                            public, frozenset(settings.model_denied_addresses))
    lease = record.attempt_lease()
    control = ControlPlaneClient(settings.control_endpoint, settings.worker_id, settings.worker_key.get_secret_value(),
                                 ca_file=settings.control_ca_file)
    admission = None
    factory = None
    if settings.sandbox is not None:
        configuration = settings.sandbox
        admission = HostAdmissionClient(lease, configuration.admission_socket, configuration.admission_capability.get_secret_value())
        connection = sandbox_connection(configuration.endpoint, configuration.credential.get_secret_value(), ca_file=configuration.ca_file)

        def factory(bound, permit):
            if bound != lease:
                raise RuntimeError('executor_configuration_invalid')
            return OpenSandboxProvider(connection, DurableSandboxJournal(control, lease), admission,
                                       profile=configuration.profile, memory='384Mi', permission=permit.check)
    try:
        state, code = 'failed', 'execution_failed'
        try:
            bundle = await control.runtime(lease)
            sid.init_sid(sid.SidInfo(sub='oa', location='host', index=1, local_ip='127.0.0.1', local_port='0000'))
            span = Span(app_id='open-agent', uid=lease.run.tenant)
            span.sid = lease.run.run_id
            trace = NodeTraceLog(service_id='durable-executor', sid=lease.run.run_id, sub='agent')
            await RuntimeExecutor(control, policy=policy, sandbox_factory=factory, ca_files=settings.model_ca_files).execute(lease, bundle, span, trace)
            state, code = 'succeeded', None
        except asyncio.CancelledError:
            code = 'outcome_unknown'
        except Exception as error:
            # RunLedger resolves a concurrent cancellation/deadline/revocation before accepting this result.
            print(json.dumps({'event': 'executor.failed', **failure_location(error)}), flush=True)
        result = await control.finish(lease, state, code)
        if result.get('state') not in TERMINAL:
            raise RuntimeError('executor_completion_unconfirmed')
        return result['state']
    finally:
        if admission is not None:
            await admission.aclose()
        await control.aclose()


async def main(attempt_path: Path):
    record = SupervisedLease.model_validate(document(attempt_path))
    credentials = Path(os.environ['CREDENTIALS_DIRECTORY'])
    expected = '/run/credentials/oa-exec-' + record.leaseId.replace('-', '') + '.service'
    if str(credentials) != expected or os.geteuid() == 0:
        raise RuntimeError('executor_launch_invalid')
    settings = ExecutorSettings.model_validate(document(credentials / 'worker.json'))
    current = asyncio.current_task()
    loop = asyncio.get_running_loop()
    for event in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(event, current.cancel)
    state = await execute_attempt(settings, record)
    print(json.dumps({'event': 'executor.finished', 'run_id': record.runId, 'state': state}), flush=True)


if __name__ == '__main__':
    os.environ['LANGSMITH_TRACING'] = 'false'
    os.environ['LANGCHAIN_TRACING_V2'] = 'false'
    try:
        if len(sys.argv) != 2:
            raise RuntimeError('executor_launch_invalid')
        asyncio.run(main(Path(sys.argv[1])))
    except BaseException as error:
        print(json.dumps({'event': 'executor.stopped', 'code': 'execution_unconfirmed', **failure_location(error)}), flush=True)
        raise SystemExit(1) from None
