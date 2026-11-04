import json
from uuid import uuid4

import httpx
import pytest

from agent.durable.contracts import RunRef
from agent.durable.control import ControlPlaneClient, ControlPlaneError
from agent.durable.supervision import CleanupProof, SupervisorClient


def snapshot():
    lease = {"scope": {"tenant": "tenant", "environment": "test"}, "runId": str(uuid4()),
             "attempt": 1, "leaseId": str(uuid4()), "fencingToken": 1, "workerId": "worker"}
    return {"lease": lease, "supervisorId": "supervisor", "bootId": str(uuid4()),
            "unitName": "oa-exec-" + lease["leaseId"].replace("-", "") + ".service", "invocationId": None,
            "phase": "prepared", "state": "running", "leaseState": "held", "heartbeatUntil": "2026-11-05T01:00:00Z",
            "deadlineAt": "2026-11-05T02:00:00Z", "sandboxPhase": None, "sandboxId": None,
            "isolationProfile": None, "runtimeImage": None}


def client(respond):
    control = ControlPlaneClient("https://control.internal", "supervisor", "s" * 32,
                                 transport=httpx.MockTransport(respond))
    return control, SupervisorClient(control, "supervisor", "worker")


@pytest.mark.asyncio
async def test_claim_binds_server_lease_to_requested_run_host_and_supervisor():
    row = snapshot()
    calls = []
    def respond(request):
        calls.append(request)
        return httpx.Response(200, json={"lease": row["lease"], "state": "running", "execution": row})
    control, api = client(respond)
    try:
        run = RunRef("tenant", "test", row["lease"]["runId"])
        result = await api.claim(run, row["bootId"])
        assert result.execution.lease.attempt_lease().run == run
        assert result.execution.process().unit == row["unitName"]
        assert calls[0].headers['X-Platform-Key-Id'] == 'supervisor'
        assert json.loads(calls[0].content) == {"scope": {"tenant": "tenant", "environment": "test"}, "bootId": row["bootId"]}
    finally:
        await control.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [
    ("supervisorId", "another-host"), ("unitName", "oa-exec-" + "a" * 32 + ".service"),
    ("heartbeatUntil", "2026-11-05T01:00:00"), ("phase", "observed"), ("prompt", "PRIVATE-PROMPT"),
])
async def test_inconsistent_or_extra_response_fields_fail_without_exposing_payload(field, value):
    row = snapshot()
    row[field] = value
    control, api = client(lambda _: httpx.Response(200, json=row))
    try:
        with pytest.raises(ControlPlaneError, match="^CONTROL_SUPERVISION_INVALID$"):
            await api.inspect(row['lease']['leaseId'])
    finally:
        await control.aclose()


@pytest.mark.asyncio
async def test_unknown_one_shot_launch_ack_is_never_retried():
    calls = []
    def respond(request):
        calls.append(request)
        raise httpx.ReadTimeout('private transport details')
    control, api = client(respond)
    try:
        with pytest.raises(ControlPlaneError, match="^CONTROL_UNAVAILABLE$"):
            await api.begin(str(uuid4()))
        assert len(calls) == 1
    finally:
        await control.aclose()


@pytest.mark.asyncio
async def test_scan_rejects_foreign_worker_duplicates_and_released_rows():
    row = snapshot()
    replies = [[row, row], [{**row, "phase": "released"}], [{**row, "lease": {**row['lease'], "workerId": "other"}}]]
    control, api = client(lambda _: httpx.Response(200, json=replies.pop(0)))
    try:
        for _ in range(3):
            with pytest.raises(ControlPlaneError, match="CONTROL_SUPERVISION_INVALID"):
                await api.pending()
    finally:
        await control.aclose()


@pytest.mark.asyncio
async def test_invalid_physical_proof_is_rejected_before_sending_request():
    calls = []
    control, api = client(lambda request: calls.append(request))
    row = snapshot()
    proof = CleanupProof(proofId=str(uuid4()), kind='never_launched', bootId=row['bootId'], unitName=row['unitName'],
                         invocationId=None, sandboxId=None, evidenceSha256='a' * 64, workerStopped=True,
                         cgroupEmpty=False, sandboxAbsent=True, creationSettled=True)
    try:
        with pytest.raises(ControlPlaneError, match="CONTROL_SUPERVISION_INPUT_INVALID"):
            await api.release(row['lease']['leaseId'], proof)
        assert not calls
    finally:
        await control.aclose()


@pytest.mark.asyncio
async def test_incomplete_step_acknowledgements_cannot_authorize_next_physical_action():
    row = snapshot()
    control, api = client(lambda _: httpx.Response(200, json=row))
    proof = CleanupProof(proofId=str(uuid4()), kind='never_launched', bootId=row['bootId'], unitName=row['unitName'],
                         invocationId=None, sandboxId=None, evidenceSha256='a' * 64, workerStopped=True,
                         cgroupEmpty=True, sandboxAbsent=True, creationSettled=True)
    try:
        for action in (lambda: api.begin(row['lease']['leaseId']), lambda: api.fence(row['lease']['leaseId']),
                       lambda: api.observed(row['lease']['leaseId'], 'a' * 32),
                       lambda: api.adopt_sandbox(row['lease']['leaseId'], str(uuid4())),
                       lambda: api.release(row['lease']['leaseId'], proof)):
            with pytest.raises(ControlPlaneError, match='CONTROL_SUPERVISION_INVALID'):
                await action()
    finally:
        await control.aclose()
