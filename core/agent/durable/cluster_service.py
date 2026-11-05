"""Private, single-environment Kubernetes execution supervisor.

Configuration is operator-owned; no tenant code, model credentials or worker
credentials are loaded here. Kubernetes and Temporal use their official clients.
"""
from __future__ import annotations

import asyncio
from datetime import timedelta
import ipaddress
import json
import logging
import os
from pathlib import Path
import signal
import sys
import time
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator
from temporalio.api.enums.v1 import NamespaceState
from temporalio.api.workflowservice.v1 import DescribeNamespaceRequest
from temporalio.client import Client
from temporalio.service import TLSConfig
from temporalio.worker import Worker

from agent.durable.cluster_control import ClusterSupervisorClient, ClusterTarget
from agent.durable.cluster_lifecycle import ClusterLifecycle
from agent.durable.cluster_process import ClusterProgram, KubernetesProcesses
from agent.durable.cluster_supervisor import ClusterSupervisor
from agent.durable.control import ControlPlaneClient
from agent.durable.dispatcher import OutboxDispatcher
from agent.durable.workflow import AgentRunWorkflow

LOG = logging.getLogger(__name__)
CONFIG_ROOT = Path('/run/open-agent/supervisor')
READY = Path('/tmp/open-agent-cluster-ready.json')


def mounted_bytes(root: Path, name: str, maximum: int = 65536) -> bytes:
    # Native Secret/ConfigMap projections contain links. Resolve only a fixed
    # mount root, disallowing escape and unbounded/non-regular configuration.
    directory = root.resolve(strict=True)
    path = (directory / name).resolve(strict=True)
    if not path.is_relative_to(directory) or not path.is_file() or path.stat().st_size > maximum:
        raise RuntimeError('cluster_configuration_invalid')
    with path.open('rb') as stream:
        data = stream.read(maximum + 1)
    if len(data) > maximum:
        raise RuntimeError('cluster_configuration_invalid')
    return data


class Identity(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    id: Annotated[str, Field(pattern=r'^[A-Za-z0-9_.:-]{1,128}$')]
    key: SecretStr = Field(repr=False, min_length=32)


class TemporalSettings(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    address: Annotated[str, Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9.-]{0,252}:[1-9][0-9]{0,4}$')]
    namespace: Annotated[str, Field(pattern=r'^[A-Za-z0-9_.-]{1,128}$')]
    task_queue: Annotated[str, Field(pattern=r'^open-agent-[A-Za-z0-9_.-]{1,100}$')]
    tls_mount: Path | None = None

    @model_validator(mode='after')
    def private_transport(self):
        hostname, port = self.address.rsplit(':', 1)
        try:
            loopback = ipaddress.ip_address(hostname).is_loopback
        except ValueError:
            loopback = False
        if int(port) > 65535 or self.tls_mount is None and not loopback:
            raise ValueError('Temporal requires mTLS outside literal loopback')
        if self.tls_mount is not None and self.tls_mount.as_posix() != '/run/open-agent/temporal':
            raise ValueError('Unexpected Temporal certificate mount')
        return self

    def tls(self) -> TLSConfig | None:
        if self.tls_mount is None:
            return None
        return TLSConfig(server_root_ca_cert=mounted_bytes(self.tls_mount, 'ca.pem'),
                         client_cert=mounted_bytes(self.tls_mount, 'tls.crt'),
                         client_private_key=mounted_bytes(self.tls_mount, 'tls.key'))


class Settings(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    control_endpoint: str
    control_ca_file: str | None = None
    supervisor: Identity
    dispatcher: Identity
    target: ClusterTarget
    program: ClusterProgram
    temporal: TemporalSettings
    maximum_activities: Annotated[int, Field(strict=True, ge=2, le=128)] = 16

    @model_validator(mode='after')
    def identities(self):
        if len({self.supervisor.id, self.dispatcher.id, self.target.workerId}) != 3:
            raise ValueError('Execution service identities must be distinct')
        return self


def check_ready(path: Path = READY) -> bool:
    if os.name != 'posix':
        return False
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 4096:
            return False
        state = json.loads(path.read_bytes())
        age = time.monotonic() - state['monotonic']
        if type(state['pid']) is not int or state['pid'] < 1 or not 0 <= age <= 20:
            return False
        os.kill(state['pid'], 0)
        return True
    except (OSError, ValueError, KeyError, TypeError):
        return False


async def verify_temporal_namespace(temporal: Client, namespace: str):
    value = await temporal.workflow_service.describe_namespace(
        DescribeNamespaceRequest(namespace=namespace), timeout=timedelta(seconds=5))
    if value.namespace_info.name != namespace or value.namespace_info.state != NamespaceState.NAMESPACE_STATE_REGISTERED:
        raise RuntimeError('cluster_temporal_namespace_unavailable')


async def serve(settings: Settings, *, processes=None, stop: asyncio.Event | None = None, ready_path: Path = READY):
    """Injectable process adapter only for tests; normal entry uses in-cluster auth."""
    stop = stop or asyncio.Event()
    if ready_path.is_symlink():
        raise RuntimeError('cluster_readiness_path_invalid')
    ready_path.unlink(missing_ok=True)
    tasks = []
    controls = []
    try:
        processes = processes or KubernetesProcesses.in_cluster(settings.target, settings.program)
        if processes.target != settings.target or processes.program != settings.program:
            raise RuntimeError('cluster_configuration_mismatch')
        await processes.verify_target()
        for identity in (settings.supervisor, settings.dispatcher):
            controls.append(ControlPlaneClient(settings.control_endpoint, identity.id,
                identity.key.get_secret_value(), ca_file=settings.control_ca_file))
        api = ClusterSupervisorClient(controls[0], settings.supervisor.id, settings.target)
        supervisor = ClusterSupervisor(api, ClusterLifecycle(api, processes))
        temporal = await Client.connect(settings.temporal.address, namespace=settings.temporal.namespace,
            tls=settings.temporal.tls(), connect_timeout=timedelta(seconds=10))
        await verify_temporal_namespace(temporal, settings.temporal.namespace)
        dispatcher = OutboxDispatcher(controls[1], temporal, settings.temporal.task_queue,
                                      environment=settings.target.environment)
        last_dispatch_success = 0.0

        async def dispatch_once():
            nonlocal last_dispatch_success
            await dispatcher.once()
            last_dispatch_success = time.monotonic()
        # Unlike a single-host owner, a new cluster replica must not fence every
        # existing process. Healthy leases may belong to another live replica.
        await supervisor.scan_once()

        async def repeated(name, operation, interval):
            while not stop.is_set():
                try:
                    await operation()
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    LOG.warning('Cluster dependency unavailable loop=%s error_type=%s', name, type(error).__name__)
                try:
                    await asyncio.wait_for(stop.wait(), timeout=interval)
                except TimeoutError:
                    pass

        async def health():
            try:
                await processes.verify_target()
                await api.pending(limit=1)
                await verify_temporal_namespace(temporal, settings.temporal.namespace)
                if not await temporal.service_client.check_health(timeout=timedelta(seconds=5)):
                    raise RuntimeError('cluster_temporal_unavailable')
                if time.monotonic() - last_dispatch_success > 15:
                    raise RuntimeError('cluster_dispatch_unavailable')
                body = {'pid': os.getpid(), 'monotonic': time.monotonic(),
                        'environment': settings.target.environment, 'task_queue': settings.temporal.task_queue}
                temporary = ready_path.with_suffix('.pending')
                descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
                with os.fdopen(descriptor, 'w') as stream:
                    json.dump(body, stream)
                os.replace(temporary, ready_path)
            except BaseException:
                ready_path.unlink(missing_ok=True)
                raise

        async with Worker(temporal, task_queue=settings.temporal.task_queue, workflows=[AgentRunWorkflow],
            activities=[supervisor.execute, supervisor.reconcile], max_concurrent_activities=settings.maximum_activities,
            max_concurrent_workflow_tasks=16, graceful_shutdown_timeout=timedelta(seconds=20)):
            await dispatch_once()
            await health()
            tasks = [asyncio.create_task(repeated('dispatch', dispatch_once, 1)),
                     asyncio.create_task(repeated('cleanup', supervisor.scan_once, 5)),
                     asyncio.create_task(repeated('health', health, 5))]
            LOG.info('Cluster supervisor ready environment=%s queue=%s', settings.target.environment, settings.temporal.task_queue)
            try:
                await stop.wait()
            finally:
                ready_path.unlink(missing_ok=True)
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        ready_path.unlink(missing_ok=True)
        stop.set()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for control in controls:
            await control.aclose()
        if processes is not None:
            await processes.close()


async def main():
    if os.name != 'posix' or os.geteuid() != 10001:
        raise RuntimeError('cluster_service_identity_invalid')
    settings = Settings.model_validate_json(mounted_bytes(CONFIG_ROOT, 'service.json'))
    stop = asyncio.Event()
    for event in (signal.SIGTERM, signal.SIGINT):
        asyncio.get_running_loop().add_signal_handler(event, stop.set)
    await serve(settings, stop=stop)


if __name__ == '__main__':
    if sys.argv[1:] == ['--check']:
        raise SystemExit(0 if check_ready() else 1)
    os.environ['LANGSMITH_TRACING'] = 'false'
    os.environ['LANGCHAIN_TRACING_V2'] = 'false'
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')
    logging.getLogger('httpx').setLevel(logging.WARNING)
    logging.getLogger('temporalio.activity').setLevel(logging.ERROR)
    try:
        if sys.argv[1:]:
            raise RuntimeError('cluster_service_arguments_invalid')
        asyncio.run(main())
    except BaseException as error:
        LOG.error('Cluster supervisor stopped error_type=%s', type(error).__name__)
        raise SystemExit(1) from None
