"""Only for the disposable zero-node fixture. Force cleanup here is NOT stop evidence."""
from pathlib import Path
import json
import sys
from uuid import uuid4
root=Path('/opt/open-agent/k3s-api-fixture')
m=json.loads((Path(__file__).resolve().parent/'cluster-contract-manifest.json').read_text())
folder=Path('/opt/open-agent/cluster-contracts')/m['sha256']
sys.path[:0]=[str(folder/'source'),str(folder/'vendor')]
from kubernetes import client,config
from kubernetes.client.exceptions import ApiException
from urllib3.util.retry import Retry
from agent.durable.cluster_process import KubernetesProcesses,ClusterProgram,FINALIZER
from agent.durable.cluster_control import ClusterSnapshot
from agent.tests.test_cluster_control import snapshot
cfg=client.Configuration();config.load_kube_config(config_file=str(root/'admin.yaml'),client_configuration=cfg,persist_config=False)
cfg.retries=Retry(total=0,connect=0,read=0,redirect=0,status=0)
api=client.ApiClient(cfg);core=client.CoreV1Api(api)
prior=json.loads((root/'verification.json').read_text());namespace=prior['cases'][0]['namespace']
raw=snapshot();raw['target'].update(clusterId=core.read_namespace('kube-system').metadata.uid,namespace=namespace,namespaceUid=core.read_namespace(namespace).metadata.uid)
row=ClusterSnapshot.model_validate(raw).model_copy(update={'phase':'launching'})
processes=KubernetesProcesses(api,row.target,ClusterProgram('worker','executor','trusted'))
created=[];result={}
try:
    for bound in (True,False):
        body=processes.manifest(row);body['metadata']['name']='oa-barrier-'+uuid4().hex[:8]
        body['spec'].pop('schedulingGates')
        if bound:body['spec']['nodeName']='fixture-node-absent'
        pod=core.create_namespaced_pod(namespace,body);created.append((pod.metadata.name,pod.metadata.uid))
        if bound:
            try:core.patch_namespaced_pod(pod.metadata.name,namespace,[{'op':'test','path':'/metadata/uid','value':pod.metadata.uid},{'op':'replace','path':'/spec/nodeName','value':''}],_content_type='application/json-patch+json')
            except ApiException as error:result['clear_assigned_node_status']=error.status
            else:raise RuntimeError('assigned_node_was_clearable')
        else:
            core.delete_namespaced_pod(pod.metadata.name,namespace,body=client.V1DeleteOptions(grace_period_seconds=30,preconditions=client.V1Preconditions(uid=pod.metadata.uid)))
            deleted=core.read_namespaced_pod(pod.metadata.name,namespace)
            assert deleted.metadata.deletion_timestamp and not deleted.spec.node_name and not deleted.status.container_statuses
            try:core.create_namespaced_pod_binding(pod.metadata.name,namespace,{'metadata':{'name':pod.metadata.name,'uid':pod.metadata.uid},'target':{'kind':'Node','name':'fixture-node-absent'}})
            except ApiException as error:result['ungated_binding_after_delete_status']=error.status
            else:raise RuntimeError('ungated_deleting_pod_was_bound')
    result['real_api_unbound_barrier_verified']=True
    (root/'unbound-barrier.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result))
finally:
    assert not core.list_node().items
    for name,uid in created:
        try:
            core.delete_namespaced_pod(name,namespace,body=client.V1DeleteOptions(grace_period_seconds=0,preconditions=client.V1Preconditions(uid=uid)))
            pod=core.read_namespaced_pod(name,namespace)
            core.patch_namespaced_pod(name,namespace,[{'op':'test','path':'/metadata/uid','value':uid},
                {'op':'test','path':'/metadata/resourceVersion','value':pod.metadata.resource_version},
                {'op':'replace','path':'/metadata/finalizers','value':[]}],_content_type='application/json-patch+json')
        except ApiException as error:
            if error.status!=404:raise
    api.close()
