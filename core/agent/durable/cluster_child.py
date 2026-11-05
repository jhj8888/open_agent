"""Non-root trusted executor entry point; no Kubernetes token or reconciler key.

The lease is public binding metadata from the Pod spec. Worker configuration is
mounted by the operator; runtime inputs/model secrets are fetched under its lease.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import signal
from uuid import UUID

from agent.durable.child import ExecutorSettings, document, execute_attempt, failure_location
from agent.durable.supervision import SupervisedLease


def configuration() -> tuple[ExecutorSettings, SupervisedLease]:
    if os.name != "posix" or os.geteuid() != 10001:
        raise RuntimeError("cluster_executor_identity_invalid")
    uid = os.environ.get("OPEN_AGENT_POD_UID", "")
    if str(UUID(uid)) != uid:
        raise RuntimeError("cluster_executor_identity_invalid")
    raw = os.environ.get("OPEN_AGENT_LEASE", "")
    if len(raw.encode()) > 4096:
        raise RuntimeError("cluster_executor_configuration_invalid")
    record = SupervisedLease.model_validate_json(raw)
    # Native Kubernetes Secret projection uses symlinks. Resolve the fixed
    # operator mount; no tenant-controlled pathname is ever accepted.
    root = Path("/run/open-agent/worker").resolve(strict=True)
    path = (root / "worker.json").resolve(strict=True)
    if not path.is_relative_to(root):
        raise RuntimeError("cluster_executor_configuration_invalid")
    template = document(path)
    if not isinstance(template, dict) or "lease_id" in template:
        raise RuntimeError("cluster_executor_configuration_invalid")
    settings = ExecutorSettings.model_validate({**template, "lease_id": record.leaseId})
    if settings.worker_id != record.workerId:
        raise RuntimeError("cluster_executor_identity_invalid")
    # Host Unix-socket admission is not a cluster isolation proof. The cluster
    # sandbox admission/cleanup service must be connected before enabling Skills.
    if settings.sandbox is not None:
        raise RuntimeError("cluster_sandbox_admission_unavailable")
    return settings, record


async def main():
    settings, record = configuration()
    current = asyncio.current_task()
    loop = asyncio.get_running_loop()
    for event in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(event, current.cancel)
    result = await execute_attempt(settings, record)
    print(json.dumps({"event": "executor.finished", "run_id": record.runId, "state": result}), flush=True)


if __name__ == "__main__":
    os.environ["LANGSMITH_TRACING"] = "false"
    os.environ["LANGCHAIN_TRACING_V2"] = "false"
    try:
        asyncio.run(main())
    except BaseException as error:
        print(json.dumps({"event": "executor.stopped", "code": "execution_unconfirmed", **failure_location(error)}), flush=True)
        raise SystemExit(1) from None
