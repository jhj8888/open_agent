"""Environment routing must fail before Temporal effects or outbox acknowledgement."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import httpx
import pytest

from agent.durable.contracts import RunRef
from agent.durable.control import ControlPlaneClient, ControlPlaneError, Delivery
from agent.durable.dispatcher import OutboxDispatcher
from agent.durable.service import ScopedActivities, ServiceSettings
from agent.durable.contracts import ReconcileRequest, RunResult
from temporalio.exceptions import ApplicationError


def wire(environment='prod'):
    return {'id':1,'tenant':'tenant','environment':environment,'runId':str(uuid4()),
            'operation':'start','claimId':str(uuid4())}


@pytest.mark.asyncio
async def test_control_sends_explicit_environment_and_legacy_default_is_test_only():
    requests=[]
    def respond(request):
        body=json.loads(request.content);requests.append(body)
        return httpx.Response(200,json=[wire(body['environment'])])
    client=ControlPlaneClient('https://control.internal','dispatch','k'*32,transport=httpx.MockTransport(respond))
    try:
        assert (await client.deliveries(environment='prod',limit=4))[0].run.environment=='prod'
        assert (await client.deliveries(limit=2))[0].run.environment=='test'
        assert requests==[{'limit':4,'leaseSeconds':60,'environment':'prod'},
                          {'limit':2,'leaseSeconds':60,'environment':'test'}]
    finally:await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize('change',['foreign','duplicate','oversized','invalid_operation','object','null'])
async def test_control_rejects_foreign_or_ambiguous_delivery_batches(change):
    row=wire();body=[row]
    if change=='foreign':row['environment']='test'
    elif change=='duplicate':body=[row,row]
    elif change=='oversized':body=[{**row,'id':i+1} for i in range(5)]
    elif change=='invalid_operation':row['operation']='execute'
    elif change=='object':body={'private':'not a batch'}
    elif change=='null':body=None
    client=ControlPlaneClient('https://control.internal','dispatch','k'*32,
                             transport=httpx.MockTransport(lambda request:httpx.Response(200,json=body)))
    try:
        with pytest.raises(ControlPlaneError):await client.deliveries(environment='prod',limit=4)
    finally:await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize('operation,state',[('start','queued'),('cancel','cancel_requested'),('start','succeeded')])
async def test_foreign_delivery_never_reads_state_signals_starts_or_acknowledges(operation,state):
    control=SimpleNamespace(state=AsyncMock(return_value={'state':state}),acknowledge=AsyncMock())
    temporal=SimpleNamespace(start_workflow=AsyncMock(),get_workflow_handle=Mock())
    dispatcher=OutboxDispatcher(control,temporal,'open-agent-test',environment='test')
    delivery=Delivery(1,RunRef('tenant','prod',str(uuid4())),operation,str(uuid4()))
    with pytest.raises(ValueError,match='environment mismatch'):await dispatcher.deliver(delivery)
    control.state.assert_not_awaited();control.acknowledge.assert_not_awaited()
    temporal.start_workflow.assert_not_awaited();temporal.get_workflow_handle.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('environment',['','all','TEST','../prod'])
async def test_invalid_environment_never_contacts_control_or_temporal(environment):
    with pytest.raises(ValueError):OutboxDispatcher(None,None,'queue',environment=environment)
    requests=[]
    client=ControlPlaneClient('https://control.internal','dispatch','k'*32,
        transport=httpx.MockTransport(lambda request:requests.append(request)))
    try:
        with pytest.raises(ValueError):await client.deliveries(environment=environment)
        assert not requests
    finally:await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize('operation',['execute','reconcile'])
async def test_queue_scope_cannot_execute_or_reconcile_foreign_task(operation):
    host=SimpleNamespace(execute=AsyncMock(),reconcile=AsyncMock())
    activity=ScopedActivities(host,'test');run=RunRef('tenant','prod',str(uuid4()))
    value=run if operation=='execute' else ReconcileRequest(run,'outcome_unknown')
    with pytest.raises(ApplicationError,match='activity_environment_mismatch'):
        await getattr(activity,operation)(value)
    host.execute.assert_not_awaited();host.reconcile.assert_not_awaited()


@pytest.mark.asyncio
async def test_scoped_activity_keeps_shared_host_capacity_and_recovery_implementation():
    host=SimpleNamespace(execute=AsyncMock(return_value=RunResult('succeeded')),
                         reconcile=AsyncMock(return_value=RunResult('cancelled')))
    activity=ScopedActivities(host,'prod');run=RunRef('tenant','prod',str(uuid4()))
    request=ReconcileRequest(run,'cancel_requested')
    assert (await activity.execute(run)).state=='succeeded'
    assert (await activity.reconcile(request)).state=='cancelled'
    host.execute.assert_awaited_once_with(run);host.reconcile.assert_awaited_once_with(request)


def test_demo_routes_preserve_explicit_production_queue_and_reject_collisions():
    body={'control_endpoint':'https://control.internal','supervisor':{'id':'supervisor','key':'s'*32},
          'dispatcher':{'id':'dispatcher','key':'d'*32},'worker_template':{},'source':'/source','python':'/python'}
    settings=ServiceSettings.model_validate(body)
    assert settings.routes()=={'test':'open-agent-demo-v1'}
    settings=ServiceSettings.model_validate({**body,'production_task_queue':'open-agent-demo-prod-v1'})
    assert settings.routes()=={'test':'open-agent-demo-v1','prod':'open-agent-demo-prod-v1'}
    with pytest.raises(ValueError):ServiceSettings.model_validate({**body,'production_task_queue':'open-agent-demo-v1'})
