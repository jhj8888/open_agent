"""Owned Java/MySQL fixture only: real worker SDK and private HTTP, synthetic model.

The fixture injects protocol v1 solely in its transient RuntimeBundle. Production API
does not emit this flag until the remaining enforcement/diagnostic gates are delivered.
"""
import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace
import sys

from common.otlp import sid
from common.otlp.trace.span import Span
from agent.domain.models.endpoint_policy import EndpointPolicy, parse_endpoint
from agent.durable.control import ControlPlaneClient, ControlPlaneError
from agent.durable.contracts import RunRef
from agent.durable.executor import RuntimeExecutor


class RecordingControl(ControlPlaneClient):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.operations=[]
        self.drop_dispatch=os.environ["BUDGET_FIXTURE_SCENARIO"]=="lost_dispatch_reply"

    async def model_budget(self, lease, operation, identifier, sha, **usage):
        result=await super().model_budget(lease,operation,identifier,sha,**usage)
        self.operations.append({"operation":operation,"call_id":identifier,"phase":result.get("phase")})
        if operation=="dispatch" and self.drop_dispatch:
            self.drop_dispatch=False
            raise ControlPlaneError("CONTROL_UNAVAILABLE")  # Commit succeeded; worker loses its receipt.
        return result


async def main():
    origin=os.environ["BUDGET_FIXTURE_CONTROL"]
    if not origin.startswith("http://127.0.0.1:"):
        raise RuntimeError("invalid_fixture_endpoint")
    control=RecordingControl(origin,"fixture-worker",os.environ["BUDGET_FIXTURE_KEY"])
    ref=RunRef("space-10","test",os.environ["BUDGET_FIXTURE_RUN"])
    sid.sid_generator2=SimpleNamespace(gen=lambda:"owned-budget-fixture")
    try:
        lease=await control.claim(ref)
        assert lease is not None
        bundle=await control.runtime(lease)
        assert bundle.model.endpoint==os.environ["BUDGET_FIXTURE_MODEL"] and bundle.model.endpoint.startswith("http://127.0.0.1:")
        assert bundle.modelBudgetProtocol is None
        bundle=bundle.model_copy(update={"modelBudgetProtocol":"v1"})
        policy=EndpointPolicy(frozenset({parse_endpoint(bundle.model.endpoint)[1]}),(),allow_loopback_for_tests=True)
        failed=False
        try:
            usage=await RuntimeExecutor(control,policy=policy).execute(lease,bundle,Span(app_id="budget-fixture"),SimpleNamespace(trace=[]))
        except Exception:
            failed=True;usage=None
        await control.finish(lease,"failed" if failed else "succeeded","model_failed" if failed else None)
        report={"scenario":os.environ["BUDGET_FIXTURE_SCENARIO"],"failed":failed,"usage":usage,
                "operations":control.operations,"run_id":ref.run_id,"lease_id":lease.lease_id,
                "runtime_protocol_injected_only_in_owned_fixture":True}
        Path(os.environ["BUDGET_FIXTURE_REPORT"]).write_text(json.dumps(report),encoding="utf-8")
    finally:
        await control.aclose()


if __name__=="__main__":
    try:
        asyncio.run(main())
    except BaseException as error:
        print(json.dumps({"fixture_failed":type(error).__name__}),file=sys.stderr)
        raise SystemExit(1) from None
