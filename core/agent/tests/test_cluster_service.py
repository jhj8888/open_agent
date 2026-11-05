import asyncio
import json
import os
from pathlib import Path
import time
from uuid import uuid4

import httpx
import pytest
from temporalio.testing import WorkflowEnvironment
from temporalio.service import RPCError

from agent.durable import cluster_service as service
from agent.durable.control import ControlPlaneClient, ControlPlaneError
from agent.tests.test_cluster_process import system
from agent.tests.test_cluster_supervisor import Ledger
from agent.tests.test_durable_workflow import binary


def settings(row, driver, address='127.0.0.1:7233'):
    return service.Settings(control_endpoint='http://127.0.0.1:18082',
        supervisor={'id':row.supervisorId,'key':'fixture-supervisor-secret-value-12345'},
        dispatcher={'id':'dispatcher','key':'fixture-dispatcher-secret-value-67890'},
        target=row.target,program=driver.program,
        temporal={'address':address,'namespace':'default','task_queue':'open-agent-cluster-fixture'})


@pytest.mark.parametrize('address', ['temporal.private:7233','localhost:7233','127.0.0.1:65536',
    'https://127.0.0.1:7233','user:secret@temporal:7233'])
def test_temporal_rejects_unencrypted_network_or_ambiguous_address(address):
    with pytest.raises(ValueError):
        service.TemporalSettings(address=address,namespace='default',task_queue='open-agent-test')


def test_certificate_mount_is_fixed_and_private_keys_never_in_configuration_repr(system):
    row, driver, _ = system
    config=settings(row,driver)
    assert 'fixture-supervisor-secret-value' not in repr(config) and 'fixture-dispatcher-secret-value' not in repr(config)
    assert config.temporal.tls() is None
    assert service.TemporalSettings(address='temporal.private:7233',namespace='test',task_queue='open-agent-test',
        tls_mount='/run/open-agent/temporal').tls_mount == Path('/run/open-agent/temporal')
    with pytest.raises(ValueError):
        service.TemporalSettings(address='temporal.private:7233',namespace='test',task_queue='open-agent-test',tls_mount='/tmp/tenant')
    with pytest.raises(ValueError):
        config.model_validate({**config.model_dump(),'dispatcher':config.supervisor})


def test_bounded_configuration_reads_only_regular_files_inside_mount(tmp_path):
    root=tmp_path/'mount';root.mkdir()
    (root/'service.json').write_text('{}')
    assert service.mounted_bytes(root,'service.json') == b'{}'
    (tmp_path/'outside').write_text('private')
    with pytest.raises(RuntimeError,match='cluster_configuration_invalid'):
        service.mounted_bytes(root,'../outside')
    with pytest.raises(RuntimeError,match='cluster_configuration_invalid'):
        service.mounted_bytes(root,'service.json',1)
    with pytest.raises(RuntimeError,match='cluster_configuration_invalid'):
        service.mounted_bytes(root,'.')


@pytest.mark.skipif(os.name!='posix',reason='Linux Secret projection and process probe')
def test_projected_secret_and_readiness_probe_fail_closed(tmp_path):
    root=tmp_path/'mount';root.mkdir()
    version=root/'..version';version.mkdir();(version/'service.json').write_text('{}')
    (root/'..data').symlink_to(version,target_is_directory=True)
    (root/'service.json').symlink_to('..data/service.json')
    assert service.mounted_bytes(root,'service.json') == b'{}'
    ready=tmp_path/'ready.json'
    ready.write_text(json.dumps({'pid':os.getpid(),'monotonic':time.monotonic()}))
    assert service.check_ready(ready)
    ready.write_text(json.dumps({'pid':os.getpid(),'monotonic':time.monotonic()-21}))
    assert not service.check_ready(ready)
    ready.write_text(json.dumps({'pid':0,'monotonic':time.monotonic()}))
    assert not service.check_ready(ready)


@pytest.mark.asyncio
@pytest.mark.skipif(os.name!='posix',reason='Linux readiness file and service integration')
@pytest.mark.parametrize('startup_failure',['none','dispatcher','namespace'])
async def test_service_dispatches_with_real_temporal_and_removes_readiness_on_shutdown(system,tmp_path,monkeypatch,startup_failure):
    row,driver,server=system
    ledger=Ledger(row)
    calls=[]
    delivery_id=str(uuid4())
    delivered=False
    async def transport(request):
        nonlocal delivered
        route=request.url.path;body=json.loads(request.content)
        calls.append((route,request.headers['X-Platform-Key-Id']))
        if route.endswith('/dispatch/claim'):
            if startup_failure == 'dispatcher':
                return httpx.Response(403,json={'error':'not allowed'})
            assert body['environment']==row.target.environment
            data=[] if delivered else [{'id':1,'tenant':row.lease.scope.tenant,'environment':row.target.environment,
                'runId':row.lease.runId,'operation':'start','claimId':delivery_id}]
            delivered=True
        elif route.endswith('/status'):
            data={'state':ledger.row.state}
        elif route.endswith('/ack'):
            data={'accepted':True}
        elif route.endswith('/pending'):
            data=[value.model_dump(mode='json') for value in await ledger.pending(body.get('after'),body['limit'])]
        else:
            operation=route.rsplit('/',1)[1]
            if operation=='claim':
                result=await ledger.claim(row.lease.attempt_lease().run)
            elif operation=='observed':
                result=await ledger.observed(row.lease.leaseId,body['podUid'],body['resourceVersion'])
            elif operation=='terminated':
                from agent.durable.cluster_control import PodTermination
                result=await ledger.terminated(row.lease.leaseId,PodTermination.model_validate(body))
            elif operation=='release':
                from agent.durable.cluster_control import ClusterCleanupProof
                result=await ledger.release(row.lease.leaseId,ClusterCleanupProof.model_validate(body))
            elif operation=='lookup':
                result=await ledger.lookup(row.lease.attempt_lease().run)
            else:
                result=await getattr(ledger,operation)(row.lease.leaseId)
            data=result.model_dump(mode='json')
        return httpx.Response(200,json=data)
    monkeypatch.setattr(service,'ControlPlaneClient',lambda *a,**kw:ControlPlaneClient(*a,**kw,transport=httpx.MockTransport(transport)))
    async with await WorkflowEnvironment.start_local(dev_server_existing_path=binary()) as env:
        config=settings(row,driver,env.client.service_client.config.target_host)
        if startup_failure == 'namespace':
            config=config.model_copy(update={'temporal':config.temporal.model_copy(update={'namespace':'unregistered-fixture'})})
        stop=asyncio.Event();ready=tmp_path/'ready.json'
        task=asyncio.create_task(service.serve(config,processes=driver,stop=stop,ready_path=ready))
        if startup_failure != 'none':
            with pytest.raises(RPCError if startup_failure=='namespace' else ControlPlaneError):
                await asyncio.wait_for(task,15)
            assert not ready.exists() and not ledger.events and server.pod is None
            return
        try:
            await asyncio.wait_for(ledger.launched.wait(),10)
            for _ in range(100):
                if not server.pod['spec'].get('schedulingGates'):
                    break
                await asyncio.sleep(.01)
            assert service.check_ready(ready)
            server.terminated();ledger.row=ledger.row.model_copy(update={'state':'succeeded'})
            async with asyncio.timeout(15):
                while ledger.row.phase!='released':
                    await asyncio.sleep(.1)
            assert ledger.events.count('begin')==1 and len(ledger.proofs)==1
        finally:
            stop.set()
            await asyncio.wait_for(task,30)
        assert not ready.exists()
    assert all(identity=='dispatcher' if '/dispatch/' in path else identity==row.supervisorId for path,identity in calls)
