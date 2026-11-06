"""Real SQL/Java HTTP/Temporal dispatch recovery; explicit synthetic activities only.

Started by the owned MySQL/JVM JUnit fixture. Configuration arrives over stdin,
never command arguments. Children execute the production OutboxDispatcher and
ControlPlaneClient; fault injection only pauses at observable commit boundaries.
"""
import asyncio
from collections import Counter
from datetime import timedelta
import json
import os
from pathlib import Path
import sys
import time
from uuid import uuid4

import httpx
from temporalio import activity
from temporalio.client import Client
from temporalio.service import RPCError, RPCStatusCode
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from agent.durable.contracts import ReconcileRequest, RunRef, RunResult
from agent.durable.control import ControlPlaneClient
from agent.durable.dispatcher import OutboxDispatcher, workflow_id
from agent.durable.workflow import AgentRunWorkflow, EXECUTE_ACTIVITY, RECONCILE_ACTIVITY


async def pause(stage):
    print(json.dumps({"stage": stage}), flush=True)
    await asyncio.Future()


class FaultControl(ControlPlaneClient):
    """The real HTTP client, paused before or after the acknowledgement RPC."""
    def __init__(self, config):
        super().__init__(config["api"], "fixture-dispatcher", config["dispatcher_key"])
        self.mode = config["mode"]

    async def acknowledge(self, delivery):
        if self.mode == "start_before_ack":
            await pause("temporal_accepted_before_sql_ack")
        result = await super().acknowledge(delivery)
        if self.mode == "ack_before_return":
            assert result
            await pause("sql_ack_committed_before_return")
        return result


async def child(config):
    control = FaultControl(config)
    try:
        temporal = await Client.connect(config["temporal_address"])
        if config["mode"] == "claim_before_start":
            deliveries = await control.deliveries(limit=1)
            assert len(deliveries) == 1
            await pause("sql_claimed_before_temporal")
        else:
            accepted = await OutboxDispatcher(control, temporal, config["queue"]).once()
            print(json.dumps({"stage": "completed", "acknowledged": accepted}), flush=True)
    finally:
        await control.aclose()


class LedgerActivities:
    """Synthetic effect, but real worker claim/permit/event/finish HTTP and SQL."""
    def __init__(self, config):
        self.control = ControlPlaneClient(config["api"], "fixture-worker", config["worker_key"])
        self.calls = Counter()
        self.cleanups = Counter()
        self.started = {}
        self.release = {}

    @activity.defn(name=EXECUTE_ACTIVITY)
    async def execute(self, run: RunRef) -> RunResult:
        self.calls[run.run_id] += 1
        assert activity.info().attempt == 1 and self.calls[run.run_id] == 1
        lease = await self.control.claim(run)
        assert lease is not None and await self.control.permit(lease)
        await self.control.append(lease, "fixture-effect", "execution.notice", {"fixture": True})
        self.started.setdefault(run.run_id, asyncio.Event()).set()
        gate = self.release.setdefault(run.run_id, asyncio.Event())
        while not gate.is_set():
            activity.heartbeat()
            assert await self.control.heartbeat(lease)
            try:
                await asyncio.wait_for(gate.wait(), 1)
            except TimeoutError:
                pass
        state = await self.control.finish(lease, "succeeded")
        assert state["state"] == "succeeded"
        return RunResult("succeeded")

    @activity.defn(name=RECONCILE_ACTIVITY)
    async def reconcile(self, request: ReconcileRequest) -> RunResult:
        self.cleanups[request.run.run_id] += 1
        activity.heartbeat()
        state = await self.control.state(request.run)
        assert state["state"] == "succeeded"
        # No execution entity was launched. Deliberately do not release slots or
        # claim this synthetic activity proves independent physical cleanup.
        return RunResult(state["state"])


class Scenario:
    def __init__(self, config, temporal):
        self.config, self.temporal = config, temporal
        self.children = []
        self.evidence = []
        self.directory = Path(config["directory"])
        self.worker = LedgerActivities(config)
        self.public = httpx.AsyncClient(base_url=config["api"], trust_env=False,
                                       timeout=15, headers={"Authorization": "Bearer " + config["token"]})

    async def start_child(self, mode):
        logfile = (self.directory / ("dispatcher-" + str(len(self.children)) + ".log")).open("wb")
        try:
            process = await asyncio.create_subprocess_exec(
                sys.executable, str(Path(__file__).resolve()), "--child",
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=logfile,
            )
        finally:
            logfile.close()
        self.children.append(process)
        configuration = {key: self.config[key] for key in ["api", "dispatcher_key", "temporal_address", "queue"]}
        configuration["mode"] = mode
        process.stdin.write(json.dumps(configuration).encode())
        await process.stdin.drain()
        process.stdin.close()
        line = await asyncio.wait_for(process.stdout.readline(), 30)
        assert line, "Dispatcher child exited before its barrier"
        result = json.loads(line)
        return process, result

    async def kill(self, process):
        assert process.returncode is None
        process.kill()
        await asyncio.wait_for(process.wait(), 10)
        assert process.returncode != 0

    async def dispatch(self, expected):
        process, result = await self.start_child("normal")
        assert result == {"stage": "completed", "acknowledged": expected}, result
        assert await asyncio.wait_for(process.wait(), 15) == 0

    async def submit(self, key):
        response = await self.public.post("/api/v1/agent-deployments/" + self.config["deployment"] + "/runs",
            headers={"Idempotency-Key": key}, json={"input": {"message": "Synthetic dispatch fixture"}, "timeoutSeconds": 3600})
        assert response.status_code == 202
        return response.json()["data"]["run_id"]

    async def started(self, run):
        await asyncio.wait_for(self.worker.started.setdefault(run, asyncio.Event()).wait(), 30)

    async def finish(self, run):
        await self.started(run)
        self.worker.release.setdefault(run, asyncio.Event()).set()
        handle = self.temporal.get_workflow_handle(workflow_id(run), result_type=RunResult)
        assert (await asyncio.wait_for(handle.result(), 30)).state == "succeeded"
        history = await handle.fetch_history()
        execute_scheduled = sum(
            event.HasField("activity_task_scheduled_event_attributes")
            and event.activity_task_scheduled_event_attributes.activity_type.name == EXECUTE_ACTIVITY
            for event in history.events
        )
        assert execute_scheduled == self.worker.calls[run] == self.worker.cleanups[run] == 1
        response = await self.public.get("/api/v1/runs/" + run)
        assert response.status_code == 200 and response.json()["data"]["state"] == "succeeded"
        return {"execute_activities_scheduled": execute_scheduled, "effect_calls": 1, "synthetic_reconcile_calls": 1}

    async def absent(self, run):
        try:
            await self.temporal.get_workflow_handle(workflow_id(run)).describe()
        except RPCError as error:
            assert error.status == RPCStatusCode.NOT_FOUND
        else:
            raise AssertionError("Unexpected Temporal workflow before delivery")

    async def wait_for_real_claim_expiry(self):
        # Production lease is 60 seconds. Do not alter SQL clocks/claim deadlines.
        started = time.monotonic()
        await asyncio.sleep(61)
        return round(time.monotonic() - started, 3)

    async def verify(self):
        async with Worker(self.temporal, task_queue=self.config["queue"], workflows=[AgentRunWorkflow],
                          activities=[self.worker.execute, self.worker.reconcile], max_concurrent_activities=4,
                          graceful_shutdown_timeout=timedelta(seconds=2), max_heartbeat_throttle_interval=timedelta(seconds=1)):
            # The Java parent already rolled back a failed transaction and killed
            # the API after the retry committed. Its first run is still pending.
            first = self.config["first_run"]
            process, event = await self.start_child("claim_before_start")
            assert event["stage"] == "sql_claimed_before_temporal"
            await self.absent(first)
            await self.kill(process)
            await self.dispatch(0)  # A second process cannot steal a live claim.
            waited = await self.wait_for_real_claim_expiry()
            await self.dispatch(1)
            result = await self.finish(first)
            self.evidence.append({"stage": "crash_after_sql_claim", "lease_expiry_wait_seconds": waited, **result})

            second = await self.submit("temporal-accepted")
            process, event = await self.start_child("start_before_ack")
            assert event["stage"] == "temporal_accepted_before_sql_ack"
            await self.started(second)
            original_execution = (await self.temporal.get_workflow_handle(workflow_id(second)).describe()).run_id
            await self.kill(process)
            await self.dispatch(0)
            waited = await self.wait_for_real_claim_expiry()
            await self.dispatch(1)  # Production REJECT_DUPLICATE path on the real server.
            assert (await self.temporal.get_workflow_handle(workflow_id(second)).describe()).run_id == original_execution
            result = await self.finish(second)
            self.evidence.append({"stage": "crash_after_temporal_before_ack", "same_temporal_run": True,
                                  "lease_expiry_wait_seconds": waited, **result})

            third = await self.submit("ack-committed")
            process, event = await self.start_child("ack_before_return")
            assert event["stage"] == "sql_ack_committed_before_return"
            await self.kill(process)
            await self.dispatch(0)
            result = await self.finish(third)
            self.evidence.append({"stage": "crash_after_sql_ack", **result})

            cancelled = await self.submit("cancel-before-dispatch")
            response = await self.public.post("/api/v1/runs/" + cancelled + "/cancel")
            assert response.status_code == 200 and response.json()["data"]["state"] == "cancelled"
            await self.dispatch(1)
            await self.absent(cancelled)
            assert cancelled not in self.worker.calls
            await self.dispatch(0)
            self.evidence.append({"stage": "cancelled_before_dispatch", "workflow_absent": True, "effect_calls": 0})

    async def close(self):
        for process in self.children:
            if process.returncode is None:
                process.kill()
            await asyncio.wait_for(process.wait(), 10)
        await self.public.aclose()
        await self.worker.control.aclose()


async def main(config):
    directory = Path(config["directory"])
    assert directory.is_dir() and directory.name.startswith("oa-multi-api-")
    binary = Path(config["temporal_binary"])
    assert binary.is_file()
    started = time.monotonic()
    scenario = None
    async with await WorkflowEnvironment.start_local(
        dev_server_existing_path=str(binary), dev_server_database_filename=str(directory / "temporal.db"),
        dev_server_log_level="error", ui=False,
    ) as environment:
        config["temporal_address"] = environment.client.service_client.config.target_host
        config["queue"] = "dispatch-fixture-" + uuid4().hex
        scenario = Scenario(config, environment.client)
        try:
            await scenario.verify()
        finally:
            await scenario.close()
    report = {"verified": True, "stages": scenario.evidence, "dispatcher_processes": len(scenario.children),
              "all_dispatcher_children_exited": all(child.returncode is not None for child in scenario.children),
              "temporal_environment_shutdown": True, "elapsed_seconds": round(time.monotonic() - started, 3),
              "scope": "Actual SQL/HTTP/dispatcher/Temporal; synthetic ledger activity, no model or sandbox, slots deliberately retained"}
    (directory / "dispatch-result.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    configuration = json.load(sys.stdin)
    asyncio.run(child(configuration) if sys.argv[1:] == ["--child"] else main(configuration))
