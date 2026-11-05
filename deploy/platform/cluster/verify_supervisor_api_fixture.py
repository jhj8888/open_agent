"""Real Temporal + agentless Kubernetes API cancellation and recovery.

The ledger is explicitly a test fixture; DB/HTTP persistence is verified by the
separate Java tests. No worker container, model or tenant code runs in this test.
"""
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from uuid import uuid4

ROOT = Path('/opt/open-agent/k3s-api-fixture')
manifest = json.loads((Path(__file__).resolve().parent / 'cluster-contract-manifest.json').read_text())
source = Path('/opt/open-agent/cluster-contracts') / manifest['sha256']
sys.path[:0] = [str(source/'source'), str(source/'vendor')]

from kubernetes import client, config
from kubernetes.client.exceptions import ApiException
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker
from urllib3.util.retry import Retry

from agent.durable.cluster_control import ClusterSnapshot
from agent.durable.cluster_lifecycle import ClusterLifecycle
from agent.durable.cluster_process import ClusterProgram, KubernetesProcesses
from agent.durable.cluster_supervisor import ClusterSupervisor
from agent.durable.contracts import RunResult
from agent.durable.workflow import AgentRunWorkflow
from agent.tests.test_cluster_control import snapshot
from agent.tests.test_cluster_supervisor import Ledger


async def main():
    os.umask(0o077)
    assert (ROOT/'owner').read_text() == 'open-agent-k3s-api-fixture-v1'
    configuration = client.Configuration()
    config.load_kube_config(config_file=str(ROOT/'admin.yaml'),client_configuration=configuration,persist_config=False)
    configuration.retries = Retry(total=0,connect=0,read=0,redirect=0,status=0)
    configuration.debug = False
    api_client = client.ApiClient(configuration)
    core = client.CoreV1Api(api_client)
    namespace = 'oa-supervisor-fixture-' + uuid4().hex[:12]
    try:
        version = client.VersionApi(api_client).get_code(_request_timeout=(5,15)).git_version
        assert version == 'v1.36.6+k3s1'
        assert not core.list_node(_request_timeout=(5,15)).items
        ns = core.create_namespace({'metadata':{'name':namespace,'labels':{'open-agent.io/fixture':'supervisor'}}},_request_timeout=(5,15))
        core.create_namespaced_service_account(namespace,{'metadata':{'name':'executor'},'automountServiceAccountToken':False},_request_timeout=(5,15))
        raw = snapshot()
        raw['target'].update(namespace=namespace,namespaceUid=ns.metadata.uid,
            clusterId=core.read_namespace('kube-system',_request_timeout=(5,15)).metadata.uid)
        row = ClusterSnapshot.model_validate(raw)
        ledger = Ledger(row)
        processes = KubernetesProcesses(api_client,row.target,ClusterProgram('executor-worker','executor','trusted'))
        supervisor = ClusterSupervisor(ledger,ClusterLifecycle(ledger,processes))
        replica = ClusterSupervisor(ledger,ClusterLifecycle(ledger,processes))
        run = row.lease.attempt_lease().run
        async with await WorkflowEnvironment.start_local(dev_server_existing_path='/opt/open-agent/runtime/temporal/1.10.0/temporal') as env:
            queue = 'open-agent-cluster-fixture-' + uuid4().hex
            async with Worker(env.client,task_queue=queue,workflows=[AgentRunWorkflow],activities=[supervisor.execute,supervisor.reconcile]):
                handle = await env.client.start_workflow(AgentRunWorkflow.run,run,id='open-agent/'+run.run_id,task_queue=queue)
                await asyncio.wait_for(ledger.launched.wait(),30)
                async with asyncio.timeout(15):
                    while True:
                        pod = await processes.inspect(ledger.row)
                        if pod and not pod.spec.scheduling_gates:
                            break
                        await asyncio.sleep(.2)
                assert not pod.spec.node_name and not (pod.status and pod.status.container_statuses)
                assert await replica.scan_once() == 0 and ledger.row.state == 'running' and 'fence' not in ledger.events
                ledger.row = ledger.row.model_copy(update={'state':'cancel_requested'})
                await handle.signal(AgentRunWorkflow.request_cancel)
                assert await asyncio.wait_for(handle.result(),60) == RunResult('cancelled')
                description = await handle.describe()
                assert description.task_queue == queue
        assert ledger.events.count('begin') == 1
        assert ledger.row.phase == 'released' and ledger.row.notStarted is not None
        assert len(ledger.proofs) == 1 and ledger.proofs[0].kind == 'never_started_pod'
        assert not core.list_namespaced_pod(namespace,_request_timeout=(5,15)).items
        assert not core.list_node(_request_timeout=(5,15)).items
        result = {'checked_at':datetime.now(timezone.utc).isoformat(),'source_archive_sha256':manifest['sha256'],
            'kubernetes_version':version,'run_id':run.run_id,'state':'cancelled','phase':'released',
            'real_temporal_workflow_completed':True,'real_kubernetes_api':True,'nodes':0,
            'single_launch_authorization':True,'second_replica_preserved_healthy_task':True,
            'never_started_receipt':ledger.row.notStarted.model_dump(mode='json'),
            'pod_absent_after_finalizer':True,'proof_kind':ledger.proofs[0].kind,
            'ledger':'synthetic orchestration fixture; separate real MySQL/HTTP tests',
            'kubelet_kata_cni_or_model_execution':False,'live_worker_environment_changed':False}
        (ROOT/'supervisor-verification.json').write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps({key:result[key] for key in ('run_id','state','phase','real_temporal_workflow_completed',
            'real_kubernetes_api','second_replica_preserved_healthy_task','pod_absent_after_finalizer')}))
    finally:
        try:
            ns = core.read_namespace(namespace,_request_timeout=(5,15))
            if ns.metadata.labels.get('open-agent.io/fixture') == 'supervisor':
                core.delete_namespace(namespace,_request_timeout=(5,15))
        except ApiException as error:
            if error.status != 404:
                raise
        finally:
            api_client.close()


if __name__ == '__main__':
    asyncio.run(main())
