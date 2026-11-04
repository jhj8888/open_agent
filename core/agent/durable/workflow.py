"""Deterministic orchestration. No HTTP, prompts, credentials or tool execution here."""

import asyncio
from datetime import timedelta
from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

with workflow.unsafe.imports_passed_through():
    from agent.durable.contracts import ReconcileRequest, RunRef, RunResult

EXECUTE_ACTIVITY = "open-agent.execute-v1"
RECONCILE_ACTIVITY = "open-agent.reconcile-v1"


@workflow.defn(name="open-agent-run-v1")
class AgentRunWorkflow:
    def __init__(self) -> None:
        self.cancel_requested = False
        self.executing: workflow.ActivityHandle[RunResult] | None = None

    @workflow.signal
    def request_cancel(self) -> None:
        self.cancel_requested = True
        if self.executing is not None:
            self.executing.cancel()

    @workflow.run
    async def run(self, run: RunRef) -> RunResult:
        try:
            while not self.cancel_requested:
                # A busy admission returns queued without performing effects. Once
                # admitted, this activity is NEVER automatically replayed on failure.
                self.executing = workflow.start_activity(
                    EXECUTE_ACTIVITY,
                    run,
                    result_type=RunResult,
                    schedule_to_start_timeout=timedelta(seconds=60),
                    start_to_close_timeout=timedelta(seconds=3660),
                    heartbeat_timeout=timedelta(seconds=30),
                    retry_policy=RetryPolicy(maximum_attempts=1),
                    cancellation_type=workflow.ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
                )
                result = await self.executing
                self.executing = None
                if result.state != "queued":
                    return result
                try:
                    await workflow.wait_condition(
                        lambda: self.cancel_requested, timeout=timedelta(seconds=2)
                    )
                except asyncio.TimeoutError:
                    pass
        except (ActivityError, asyncio.CancelledError):
            # An unknown tool outcome is not safe to retry. An idempotent cleanup
            # activity fences the database lease and reconciles physical resources.
            pass
        finally:
            self.executing = None
        return await workflow.execute_activity(
            RECONCILE_ACTIVITY,
            ReconcileRequest(
                run, "cancel_requested" if self.cancel_requested else "outcome_unknown"
            ),
            result_type=RunResult,
            start_to_close_timeout=timedelta(seconds=90),
            schedule_to_close_timeout=timedelta(hours=2),
            heartbeat_timeout=timedelta(seconds=30),
            retry_policy=RetryPolicy(
                initial_interval=timedelta(seconds=2),
                maximum_interval=timedelta(seconds=30),
                maximum_attempts=0,
            ),
        )
