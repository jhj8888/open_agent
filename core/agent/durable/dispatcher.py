"""Deliver the SQL outbox through the official Temporal client, without executing Agents."""

from datetime import timedelta
import asyncio
import logging

from temporalio.client import Client
from temporalio.common import WorkflowIDConflictPolicy, WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError

from agent.durable.contracts import TERMINAL
from agent.durable.control import ControlPlaneClient, Delivery
from agent.durable.workflow import AgentRunWorkflow

LOG = logging.getLogger(__name__)


def workflow_id(run_id: str) -> str:
    return "open-agent/" + run_id


class OutboxDispatcher:
    def __init__(self, control: ControlPlaneClient, temporal: Client, task_queue: str, *, environment: str = "test"):
        if environment not in {"test", "prod"}:
            raise ValueError("Invalid dispatcher environment")
        self.control, self.temporal, self.task_queue = control, temporal, task_queue
        self.environment = environment

    async def deliver(self, delivery: Delivery) -> bool:
        if delivery.run.environment != self.environment:
            raise ValueError("Dispatcher environment mismatch")
        state = await self.control.state(delivery.run, dispatcher=True)
        if state["state"] not in TERMINAL:
            if delivery.operation == "start":
                try:
                    await self.temporal.start_workflow(
                        AgentRunWorkflow.run,
                        delivery.run,
                        id=workflow_id(delivery.run.run_id),
                        task_queue=self.task_queue,
                        id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
                        id_conflict_policy=WorkflowIDConflictPolicy.FAIL,
                        rpc_timeout=timedelta(seconds=10),
                    )
                except WorkflowAlreadyStartedError:
                    pass  # Start committed but its reply/SQL acknowledgement was lost.
            elif delivery.operation == "cancel":
                await self.temporal.get_workflow_handle(
                    workflow_id(delivery.run.run_id)
                ).signal(
                    AgentRunWorkflow.request_cancel, rpc_timeout=timedelta(seconds=10)
                )
            else:
                raise ValueError("Invalid outbox operation")
        return await self.control.acknowledge(delivery)

    async def once(self) -> int:
        # Each delivery has at most three bounded 10-second RPCs. Process a small
        # batch concurrently so queued items do not outlive the 60-second claim.
        deliveries = await self.control.deliveries(limit=4, environment=self.environment)

        async def process(delivery: Delivery) -> int:
            try:
                return int(await self.deliver(delivery))
            except Exception as error:
                # Keep raw SDK/HTTP exception strings and credentials out of logs.
                LOG.warning(
                    "Outbox delivery failed run_id=%s error_type=%s",
                    delivery.run.run_id,
                    type(error).__name__,
                )
                try:
                    await self.control.retry_later(delivery)
                except Exception:
                    pass  # The SQL claim expires and a later dispatcher recovers it.
                return 0

        return sum(
            await asyncio.gather(*(process(delivery) for delivery in deliveries))
        )
