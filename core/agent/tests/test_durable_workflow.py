"""Official Temporal server + SDK tests. No external model or tenant code is executed."""

import asyncio
from datetime import timedelta
import os
import json
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
from temporalio import activity
from temporalio.client import Client
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from agent.durable.contracts import ReconcileRequest, RunRef, RunResult
from agent.durable.control import ControlPlaneError, Delivery
from agent.durable.dispatcher import OutboxDispatcher, workflow_id
from agent.durable.workflow import (
    AgentRunWorkflow,
    EXECUTE_ACTIVITY,
    RECONCILE_ACTIVITY,
)


def binary() -> str:
    path = os.getenv("TEMPORAL_TEST_BINARY")
    if not path:
        pytest.skip(
            "Set TEMPORAL_TEST_BINARY to the preverified official CLI; tests never auto-download"
        )
    assert Path(path).is_file()
    return path


def ref() -> RunRef:
    return RunRef("space-10", "test", str(uuid4()))


class Activities:
    def __init__(self, mode="success"):
        self.mode = mode
        self.calls = 0
        self.cleanups = 0
        self.started = asyncio.Event()
        self.stopped = asyncio.Event()
        self.unblock = asyncio.Event()
        self.terminal = None

    @activity.defn(name=EXECUTE_ACTIVITY)
    async def execute(self, run: RunRef) -> RunResult:
        self.calls += 1
        assert activity.info().attempt == 1
        activity.heartbeat()
        self.started.set()
        try:
            if self.mode == "failure":
                raise ApplicationError("outcome_unknown", non_retryable=True)
            if self.mode == "queued":
                return RunResult("queued")
            if self.mode == "busy" and self.calls < 3:
                return RunResult("queued")
            if self.mode == "heartbeat_lost":
                await self.unblock.wait()
            if self.mode == "wait":
                while not self.unblock.is_set():
                    activity.heartbeat()
                    await asyncio.sleep(0.1)
            self.terminal = (
                self.mode if self.mode in {"failed", "timed_out"} else "succeeded"
            )
            return RunResult(self.terminal)
        finally:
            self.stopped.set()

    @activity.defn(name=RECONCILE_ACTIVITY)
    async def reconcile(self, request: ReconcileRequest) -> RunResult:
        self.cleanups += 1
        activity.heartbeat()
        # Reconciliation reads the ledger; it never replaces an established
        # terminal outcome with a late cancellation or a generic cleanup result.
        return RunResult(
            self.terminal
            or ("cancelled" if request.reason == "cancel_requested" else "failed")
        )

    def worker(self, client, queue):
        return Worker(
            client,
            task_queue=queue,
            workflows=[AgentRunWorkflow],
            activities=[self.execute, self.reconcile],
            max_concurrent_activities=4,
            graceful_shutdown_timeout=timedelta(seconds=1),
            max_heartbeat_throttle_interval=timedelta(seconds=1),
        )


@pytest_asyncio.fixture
async def temporal(tmp_path):
    async with await WorkflowEnvironment.start_local(
        dev_server_existing_path=binary(),
        dev_server_database_filename=str(tmp_path / "temporal.db"),
        dev_server_log_level="error",
        ui=False,
    ) as env:
        yield env


@pytest.mark.asyncio
async def test_real_sdk_workflow_completes_and_history_contains_only_reference(
    temporal,
):
    activities, run, queue = Activities(), ref(), "test-" + uuid4().hex
    async with activities.worker(temporal.client, queue):
        handle = await temporal.client.start_workflow(
            AgentRunWorkflow.run, run, id=workflow_id(run.run_id), task_queue=queue
        )
        assert (await handle.result()).state == "succeeded"
        history = await handle.fetch_history()
        expected = {
            "tenant": run.tenant,
            "environment": run.environment,
            "run_id": run.run_id,
        }
        for event in history.events:
            if event.HasField("workflow_execution_started_event_attributes"):
                payloads = (
                    event.workflow_execution_started_event_attributes.input.payloads
                )
                assert len(payloads) == 1 and json.loads(payloads[0].data) == expected
            if event.HasField("activity_task_scheduled_event_attributes"):
                payloads = event.activity_task_scheduled_event_attributes.input.payloads
                attributes = event.activity_task_scheduled_event_attributes
                value = json.loads(payloads[0].data)
                assert len(payloads) == 1
                if attributes.activity_type.name == RECONCILE_ACTIVITY:
                    assert value == {"run": expected, "reason": "execution_completed"}
                else:
                    assert value == expected
        assert activities.calls == 1 and activities.cleanups == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["failed", "timed_out"])
async def test_returned_terminal_failure_also_reconciles_resources(temporal, state):
    activities, run, queue = Activities(state), ref(), "test-" + uuid4().hex
    async with activities.worker(temporal.client, queue):
        result = await temporal.client.execute_workflow(
            AgentRunWorkflow.run, run, id=workflow_id(run.run_id), task_queue=queue
        )
        assert result.state == state
        assert activities.calls == 1 and activities.cleanups == 1


@pytest.mark.asyncio
async def test_unknown_effect_is_not_automatically_reexecuted(temporal):
    activities, run, queue = Activities("failure"), ref(), "test-" + uuid4().hex
    async with activities.worker(temporal.client, queue):
        result = await temporal.client.execute_workflow(
            AgentRunWorkflow.run, run, id=workflow_id(run.run_id), task_queue=queue
        )
        assert result.state == "failed"
        assert activities.calls == 1 and activities.cleanups == 1


@pytest.mark.asyncio
async def test_busy_admission_waits_and_retries_only_the_nonexecuting_case(temporal):
    activities, run, queue = Activities("busy"), ref(), "test-" + uuid4().hex
    async with activities.worker(temporal.client, queue):
        result = await temporal.client.execute_workflow(
            AgentRunWorkflow.run, run, id=workflow_id(run.run_id), task_queue=queue
        )
        assert result.state == "succeeded" and activities.calls == 3


@pytest.mark.asyncio
async def test_cancel_waits_for_execution_activity_to_stop_before_reconciliation(
    temporal,
):
    activities, run, queue = Activities("wait"), ref(), "test-" + uuid4().hex
    async with activities.worker(temporal.client, queue):
        handle = await temporal.client.start_workflow(
            AgentRunWorkflow.run, run, id=workflow_id(run.run_id), task_queue=queue
        )
        await asyncio.wait_for(activities.started.wait(), 10)
        await handle.signal(AgentRunWorkflow.request_cancel)
        assert (await asyncio.wait_for(handle.result(), 15)).state == "cancelled"
        assert activities.stopped.is_set() and activities.cleanups == 1


class Control:
    def __init__(self, state="queued"):
        self.current_state = state
        self.acks = 0
        self.retries = 0
        self.fail_ack = True
        self.pending = []

    async def state(self, run, dispatcher=False):
        return {"state": self.current_state}

    async def acknowledge(self, delivery):
        self.acks += 1
        if self.fail_ack:
            self.fail_ack = False
            raise ControlPlaneError("CONTROL_UNAVAILABLE")
        return True

    async def retry_later(self, delivery):
        self.retries += 1
        return True

    async def deliveries(self, limit=10):
        return self.pending


@pytest.mark.asyncio
async def test_lost_ack_and_duplicate_delivery_use_one_temporal_execution(temporal):
    activities, run, queue = Activities("wait"), ref(), "test-" + uuid4().hex
    control = Control()
    dispatcher = OutboxDispatcher(control, temporal.client, queue)
    delivery = Delivery(1, run, "start", str(uuid4()))
    async with activities.worker(temporal.client, queue):
        with pytest.raises(ControlPlaneError):
            await dispatcher.deliver(delivery)
        await asyncio.wait_for(activities.started.wait(), 10)
        assert await dispatcher.deliver(delivery)
        activities.unblock.set()
        handle = temporal.client.get_workflow_handle(
            workflow_id(run.run_id), result_type=RunResult
        )
        assert (await handle.result()).state == "succeeded"
        # REJECT_DUPLICATE also prevents replay after workflow completion.
        assert await dispatcher.deliver(delivery)
        assert activities.calls == 1


@pytest.mark.asyncio
async def test_cancel_before_start_is_retried_and_terminal_run_needs_no_workflow(
    temporal,
):
    run, control = ref(), Control("cancel_requested")
    control.fail_ack = False
    control.pending = [Delivery(1, run, "cancel", str(uuid4()))]
    dispatcher = OutboxDispatcher(control, temporal.client, "unused")
    assert await dispatcher.once() == 0
    assert control.retries == 1 and control.acks == 0
    control.current_state = "cancelled"
    assert await dispatcher.once() == 1
    assert control.acks == 1


@pytest.mark.asyncio
async def test_missing_activity_heartbeats_fail_without_a_second_execution(temporal):
    activities, run, queue = Activities("heartbeat_lost"), ref(), "test-" + uuid4().hex
    async with activities.worker(temporal.client, queue):
        handle = await temporal.client.start_workflow(
            AgentRunWorkflow.run, run, id=workflow_id(run.run_id), task_queue=queue
        )
        try:
            assert (await asyncio.wait_for(handle.result(), 45)).state == "failed"
            assert activities.calls == 1 and activities.cleanups == 1
        finally:
            activities.unblock.set()


@pytest.mark.asyncio
async def test_server_and_worker_restart_resume_persisted_workflow_without_resubmission(
    tmp_path,
):
    database, run, queue = str(tmp_path / "restart.db"), ref(), "test-" + uuid4().hex
    first = Activities("queued")
    async with await WorkflowEnvironment.start_local(
        dev_server_existing_path=binary(),
        dev_server_database_filename=database,
        dev_server_log_level="error",
        ui=False,
    ) as env:
        async with first.worker(env.client, queue):
            handle = await env.client.start_workflow(
                AgentRunWorkflow.run, run, id=workflow_id(run.run_id), task_queue=queue
            )
            await asyncio.wait_for(first.started.wait(), 10)
            for _ in range(100):
                if (
                    "timerStartedEventAttributes"
                    in (await handle.fetch_history()).to_json()
                ):
                    break
                await asyncio.sleep(0.05)
            else:
                pytest.fail("Workflow did not persist an admission wait timer")
    second = Activities()
    async with await WorkflowEnvironment.start_local(
        dev_server_existing_path=binary(),
        dev_server_database_filename=database,
        dev_server_log_level="error",
        ui=False,
    ) as env:
        async with second.worker(env.client, queue):
            handle = env.client.get_workflow_handle(
                workflow_id(run.run_id), result_type=RunResult
            )
            assert (await asyncio.wait_for(handle.result(), 20)).state == "succeeded"
            assert second.calls == 1
