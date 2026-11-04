"""Single-host demo supervisor service; enterprise Kubernetes uses a separate host adapter."""

from __future__ import annotations

import asyncio
from datetime import timedelta
import json
import logging
import os
from pathlib import Path
import signal
import sys
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, SecretStr
from temporalio.client import Client
from temporalio.worker import Worker

from agent.durable.control import ControlPlaneClient
from agent.durable.dispatcher import OutboxDispatcher
from agent.durable.host_process import SystemdProcesses
from agent.durable.supervision import SupervisorClient
from agent.durable.supervisor import AttemptFiles, HostSupervisor
from agent.durable.workflow import AgentRunWorkflow

LOG = logging.getLogger(__name__)
READY = Path('/opt/open-agent/supervisor/ready.json')


class Identity(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    id: Annotated[str, Field(pattern=r'^[A-Za-z0-9_.:-]{1,128}$')]
    key: SecretStr = Field(repr=False)


class ServiceSettings(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    control_endpoint: str
    control_ca_file: str | None = None
    supervisor: Identity
    dispatcher: Identity
    worker_template: dict = Field(repr=False)
    source: Path
    python: Path
    temporal_address: Annotated[str, Field(pattern=r'^127\.0\.0\.1:[1-9][0-9]{0,4}$')] = '127.0.0.1:17233'
    temporal_namespace: Annotated[str, Field(pattern=r'^[A-Za-z0-9_.-]{1,128}$')] = 'default'
    task_queue: Annotated[str, Field(pattern=r'^open-agent-[A-Za-z0-9_.-]{1,100}$')] = 'open-agent-demo-v1'
    maximum: Annotated[int, Field(strict=True, ge=1, le=16)] = 2


async def serve(settings: ServiceSettings):
    if os.geteuid() != 0 or settings.source.resolve() != Path(__file__).resolve().parents[3]:
        raise RuntimeError('supervisor_launch_invalid')
    if settings.worker_template.get('control_endpoint') != settings.control_endpoint:
        raise RuntimeError('supervisor_endpoint_mismatch')
    # A sandbox-enabled deployment must install the independent verifier/cleanup adapter.
    # A missing adapter rejects configuration; it cannot silently weaken Skill isolation.
    if settings.worker_template.get('sandbox') is not None:
        raise RuntimeError('supervisor_sandbox_adapter_unavailable')
    READY.unlink(missing_ok=True)
    stop = asyncio.Event()
    for event in (signal.SIGINT, signal.SIGTERM):
        asyncio.get_running_loop().add_signal_handler(event, stop.set)
    supervisor_control = ControlPlaneClient(settings.control_endpoint, settings.supervisor.id,
                                             settings.supervisor.key.get_secret_value(), ca_file=settings.control_ca_file)
    dispatcher_control = ControlPlaneClient(settings.control_endpoint, settings.dispatcher.id,
                                             settings.dispatcher.key.get_secret_value(), ca_file=settings.control_ca_file)
    api = SupervisorClient(supervisor_control, settings.supervisor.id, settings.worker_template['worker_id'])
    host = HostSupervisor(api, SystemdProcesses(), AttemptFiles(settings.source, settings.python, settings.worker_template), maximum=settings.maximum)
    tasks = []
    try:
        temporal = await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace)
        dispatcher = OutboxDispatcher(dispatcher_control, temporal, settings.task_queue)
        # A supervisor restart never replays an already admitted effect. Fence inherited attempts first.
        await host.scan_once(startup=True)

        async def loop(name, operation, interval):
            while not stop.is_set():
                try:
                    await operation()
                except Exception as error:
                    LOG.warning('Supervisor dependency unavailable loop=%s error_type=%s', name, type(error).__name__)
                try:
                    await asyncio.wait_for(stop.wait(), timeout=interval)
                except TimeoutError:
                    pass

        async with Worker(temporal, task_queue=settings.task_queue, workflows=[AgentRunWorkflow],
                          activities=[host.execute, host.reconcile], max_concurrent_activities=8,
                          max_concurrent_workflow_tasks=8, graceful_shutdown_timeout=timedelta(seconds=20)):
            tasks = [asyncio.create_task(loop('dispatch', dispatcher.once, 1)),
                     asyncio.create_task(loop('cleanup', host.scan_once, 5))]
            LOG.info('Supervisor ready identity=%s task_queue=%s', settings.supervisor.id, settings.task_queue)
            READY.write_text(json.dumps({'pid': os.getpid(), 'task_queue': settings.task_queue,
                                         'source': str(settings.source), 'sandbox_enabled': False}) + '\n')
            READY.chmod(0o600)
            await stop.wait()
    finally:
        READY.unlink(missing_ok=True)
        stop.set()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        try:
            await host.scan_once(startup=True)
        except Exception as error:
            LOG.warning('Shutdown cleanup pending error_type=%s', type(error).__name__)
        await supervisor_control.aclose()
        await dispatcher_control.aclose()


def main():
    import fcntl
    if len(sys.argv) != 2 or os.geteuid() != 0:
        raise RuntimeError('supervisor_launch_invalid')
    path = Path(sys.argv[1])
    if path.is_symlink() or path.stat().st_uid != 0 or path.stat().st_mode & 0o077 or path.stat().st_size > 65536:
        raise RuntimeError('supervisor_configuration_unsafe')
    settings = ServiceSettings.model_validate(json.loads(path.read_bytes()))
    lock = os.open('/opt/open-agent/supervisor/service.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        asyncio.run(serve(settings))
    finally:
        os.close(lock)


if __name__ == '__main__':
    os.environ['LANGSMITH_TRACING'] = 'false'
    os.environ['LANGCHAIN_TRACING_V2'] = 'false'
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')
    logging.getLogger('httpx').setLevel(logging.WARNING)
    logging.getLogger('temporalio.activity').setLevel(logging.ERROR)
    try:
        main()
    except BaseException as error:
        LOG.error('Supervisor stopped error_type=%s', type(error).__name__)
        raise SystemExit(1) from None
