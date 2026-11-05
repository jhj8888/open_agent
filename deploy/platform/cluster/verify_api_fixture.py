from pathlib import Path
import asyncio
from datetime import datetime, timezone
import json
import sys
from uuid import uuid4

root=Path('/opt/open-agent/k3s-api-fixture')
manifest=json.loads((Path(__file__).resolve().parent/'cluster-contract-manifest.json').read_text())
source=Path('/opt/open-agent/cluster-contracts')/manifest['sha256']
sys.path[:0]=[str(source/'source'),str(source/'vendor')]
from kubernetes import client, config
from kubernetes.client.exceptions import ApiException
from urllib3.util.retry import Retry
from agent.durable.cluster_control import ClusterSnapshot
from agent.durable.cluster_process import ClusterProgram, ClusterProcessError, KubernetesProcesses, FINALIZER, SCHEDULING_GATE
from agent.tests.test_cluster_control import snapshot

async def verify(gated):
    configuration=client.Configuration()
    config.load_kube_config(config_file=str(root/'admin.yaml'),client_configuration=configuration,persist_config=False)
    configuration.retries=Retry(total=0,connect=0,read=0,redirect=0,status=0)
    configuration.debug=False
    api_client=client.ApiClient(configuration)
    core=client.CoreV1Api(api_client)
    version=client.VersionApi(api_client).get_code(_request_timeout=(5,15)).git_version
    assert version=='v1.36.6+k3s1'
    assert not core.list_node(_request_timeout=(5,15)).items
    namespace='oa-contract-'+uuid4().hex[:8]
    record={'version':version,'namespace':namespace,'kubelet_present':False,'source_sha256':manifest['sha256'], 'gate_retained':gated}
    ns=core.create_namespace({'metadata':{'name':namespace,'labels':{'open-agent.io/fixture':'true',
        'pod-security.kubernetes.io/enforce':'restricted','pod-security.kubernetes.io/enforce-version':'latest'}}})
    core.create_namespaced_service_account(namespace,{'metadata':{'name':'executor'},'automountServiceAccountToken':False})
    raw=snapshot()
    raw['target'].update(clusterId=core.read_namespace('kube-system').metadata.uid,namespace=namespace,namespaceUid=ns.metadata.uid)
    row=ClusterSnapshot.model_validate(raw).model_copy(update={'phase':'launching'})
    processes=KubernetesProcesses(api_client,row.target,ClusterProgram('worker','executor','trusted'))
    try:
        pod=await processes.launch(row)
        record['pod_uid']=pod.metadata.uid
        record['defaulted_spec']=api_client.sanitize_for_serialization(pod.spec)
        assert pod.spec.scheduling_gates[0].name==SCHEDULING_GATE and not pod.spec.node_name
        row=row.model_copy(update={'phase':'observed','podUid':pod.metadata.uid,'observedResourceVersion':pod.metadata.resource_version,
                                  'state':'cancelled','leaseState':'cleanup_pending'})
        if not gated:
            # Use the actual production activation path before the cancellation fence.
            await processes.activate(row.model_copy(update={'state':'running','leaseState':'held'}))
            pod=await processes.inspect(row)
            assert not pod.spec.scheduling_gates and not pod.spec.node_name
        # The real scheduler can update Pending conditions between GET and DELETE.
        # The driver fails closed on that conflict. A new bounded recovery pass
        # must inspect again; never retry an old resourceVersion blindly.
        record['stop_recovery_passes']=0
        for _ in range(10):
            record['stop_recovery_passes']+=1
            pod=await processes.inspect(row)
            old_version=pod.metadata.resource_version
            try:
                await processes.request_stop(row,pod)
                break
            except ClusterProcessError as error:
                if str(error)!='CLUSTER_OPERATION_UNCONFIRMED': raise
                await asyncio.sleep(.2)
        else: raise RuntimeError('stop_not_confirmed_after_bounded_recovery')
        pod=await processes.inspect(row)
        assert pod is not None and pod.metadata.deletion_timestamp is not None and FINALIZER in pod.metadata.finalizers
        try:
            core.patch_namespaced_pod(row.podName,namespace,[{'op':'test','path':'/metadata/uid','value':row.podUid},
                {'op':'test','path':'/metadata/resourceVersion','value':old_version},
                {'op':'remove','path':'/spec/schedulingGates'}],_content_type='application/json-patch+json')
        except ApiException as error: record['stale_activation_status']=error.status
        else: raise RuntimeError('stale_activation_was_accepted')
        try:
            core.create_namespaced_pod_binding(row.podName,namespace,{'metadata':{'name':row.podName,'uid':row.podUid},
                                                                     'target':{'kind':'Node','name':'fixture-node-absent'}})
        except ApiException as error: record['binding_after_delete_status']=error.status
        else: raise RuntimeError('deleting_gated_pod_binding_was_accepted')
        pod=await processes.inspect(row)
        receipt=processes.not_started_receipt(row,pod)
        # Private disk stands in for the HTTP ledger here; MySQL persistence is tested separately.
        (root/'stop-receipt.json').write_text(receipt.model_dump_json())
        row=row.model_copy(update={'phase':'not_started','notStarted':receipt})
        await processes.finalize(row)
        for _ in range(20):
            if await processes.absent_after_receipt(row): break
            await asyncio.sleep(.2)
        else: raise RuntimeError('pod_not_removed_after_finalizer')
        assert record['stale_activation_status'] in (409,422) and record['binding_after_delete_status']==409
        record.update(deletion_barrier_verified=True,persist_before_finalizer=True,unstarted_receipt=receipt.model_dump(mode='json'),
                      no_pods_remaining=not core.list_namespaced_pod(namespace).items,live_worker_environment_changed=False,
                      physical_kubelet_or_kata_verified=False,checked_at=datetime.now(timezone.utc).isoformat())
        return record
    except Exception as error:
        try:
            pod=core.read_namespaced_pod(row.podName,namespace)
            record['defaulted_spec']=api_client.sanitize_for_serialization(pod.spec)
        except Exception: pass
        record['failure_type']=type(error).__name__
        if hasattr(error,'status'): record['failure_status']=error.status
        (root/'diagnostic.json').write_text(json.dumps(record,indent=2)+'\n')
        raise
    finally:
        api_client.close()

async def main():
    records=[]
    for gated in (True,False):
        records.append(await verify(gated))
    result={'cases':records, 'passed':len(records), 'scope':'real agentless API; local receipt journal; no kubelet/Kata or HTTP ledger integration'}
    (root/'verification.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({'passed':len(records),'gated_and_ungated':True,'source_sha256':manifest['sha256']}))

asyncio.run(main())
