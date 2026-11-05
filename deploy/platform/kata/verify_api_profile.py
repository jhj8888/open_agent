"""Validate actual upstream-generated CR/Pod against the disposable agentless API.

No controller, runtime, kubelet, tenant code or model is executed. This cannot
establish the umbrella release's supported K8s/Kata/CNI compatibility matrix.
"""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time
from uuid import uuid4

ROOT = Path('/opt/open-agent/k3s-api-fixture')
INPUTS = Path(__file__).resolve().parent
overlay = json.loads((INPUTS/'cluster-contract-manifest.json').read_bytes())['sha256']
sys.path[:0] = [str(Path('/opt/open-agent/cluster-contracts')/overlay/'source'),
                str(Path('/opt/open-agent/cluster-contracts')/overlay/'vendor')]
from kubernetes import client, config
from kubernetes.client.exceptions import ApiException
from urllib3.util.retry import Retry
import yaml


def main():
    assert (ROOT/'owner').read_text() == 'open-agent-k3s-api-fixture-v1'
    cfg = client.Configuration()
    config.load_kube_config(config_file=str(ROOT/'admin.yaml'),client_configuration=cfg,persist_config=False)
    cfg.retries = Retry(total=0,connect=0,read=0,redirect=0,status=0)
    cfg.debug = False
    with client.ApiClient(cfg) as api:
        core=client.CoreV1Api(api);crds=client.ApiextensionsV1Api(api);custom=client.CustomObjectsApi(api)
        runtime=client.NodeV1Api(api);network=client.NetworkingV1Api(api)
        version=client.VersionApi(api).get_code().git_version
        assert version=='v1.36.6+k3s1' and not core.list_node().items
        evidence=json.loads((INPUTS/'kata-profile-upstream.json').read_bytes())
        workload=evidence['generated_workload'];name=workload['metadata']['namespace']
        created_crd=created_namespace=created_runtime=created_pod=created_workload=None
        try:
            raw=yaml.safe_load((INPUTS/'sandbox.opensandbox.io_batchsandboxes.yaml').read_bytes())
            created_crd=crds.create_custom_resource_definition(raw,field_validation='Strict',_request_timeout=(5,30))
            for _ in range(50):
                state=crds.read_custom_resource_definition(created_crd.metadata.name,_request_timeout=(5,15))
                if any(c.type=='Established' and c.status=='True' for c in state.status.conditions or []):break
                time.sleep(.2)
            else:raise AssertionError('CRD did not establish')
            docs=json.loads((INPUTS/'kata-profile-documents.json').read_bytes())
            namespace=docs['namespace.yaml'];namespace['metadata']['labels']['open-agent.io/fixture']='kata-profile'
            created_namespace=core.create_namespace(namespace,field_validation='Strict')
            core.create_namespaced_service_account(name,docs['service-account.yaml'],field_validation='Strict')
            policy=network.create_namespaced_network_policy(name,docs['network-policy.yaml'],field_validation='Strict')
            assert policy.spec.egress in ([],None) and policy.spec.pod_selector.match_labels in ({},None)
            core.create_namespaced_resource_quota(name,docs['resource-quota.yaml'],field_validation='Strict')
            required=set(docs['resource-quota.yaml']['spec']['hard'])
            quota_started=time.monotonic()
            # New CRD discovery and quota usage reconciliation are independent;
            # the initial update can precede discovery and need the 5m resync.
            for attempt in range(840):
                quota=core.read_namespaced_resource_quota('open-agent-sandbox-limits',name,_request_timeout=(5,15))
                if required.issubset(quota.status.hard or {}) and required.issubset(quota.status.used or {}):break
                if attempt % 30 == 0:
                    print(json.dumps({'waiting_for_quota':sorted(required-set(quota.status.used or {})),
                        'elapsed_seconds':round(time.monotonic()-quota_started,1)}),flush=True)
                time.sleep(.5)
            else:raise AssertionError('Namespace quota usage is not initialized')
            quota_wait_seconds=round(time.monotonic()-quota_started,1)
            runtime_name=workload['spec']['template']['spec']['runtimeClassName']
            created_runtime=runtime.create_runtime_class({'metadata':{'name':runtime_name},'handler':runtime_name},field_validation='Strict')
            created_workload=custom.create_namespaced_custom_object('sandbox.opensandbox.io','v1alpha1',name,'batchsandboxes',workload,field_validation='Strict')
            pod={'apiVersion':'v1','kind':'Pod',**deepcopy(workload['spec']['template'])}
            pod['metadata']['name']='oa-kata-profile-'+uuid4().hex[:12]
            pod['metadata']['namespace']=name
            pod['metadata']['ownerReferences']=[{'apiVersion':'sandbox.opensandbox.io/v1alpha1','kind':'BatchSandbox',
                'name':created_workload['metadata']['name'],'uid':created_workload['metadata']['uid'],'controller':True,'blockOwnerDeletion':True}]
            created_pod=core.create_namespaced_pod(name,pod,field_validation='Strict')
            assert not created_pod.spec.node_name and created_pod.spec.runtime_class_name==runtime_name
            assert created_pod.metadata.finalizers==['open-agent.io/sandbox-termination']
            assert created_pod.spec.containers[0].resources.limits['ephemeral-storage']=='512Mi'
            assert created_pod.spec.containers[0].resources.requests['ephemeral-storage']=='512Mi'
            # Independent namespace quota must reject an oversized Pod even without workers.
            oversized=deepcopy(pod);oversized['metadata']['name']='oa-kata-oversized-'+uuid4().hex[:12]
            oversized['spec']['containers'][0]['resources']={'requests':{'cpu':'1','memory':'20Gi','ephemeral-storage':'512Mi'},
                'limits':{'cpu':'1','memory':'20Gi','ephemeral-storage':'512Mi'}}
            try:core.create_namespaced_pod(name,oversized,field_validation='Strict')
            except ApiException as error:assert error.status==403
            else:raise AssertionError('Namespace quota did not reject oversized sandbox')
            report={'checked_at':datetime.now(timezone.utc).isoformat(),'kubernetes_version':version,
                'profile_manifest_sha256':evidence['profile_manifest_sha256'],'official_crd_accepted':True,
                'actual_provider_workload_accepted':True,'pod_template_accepted':True,
                'default_deny_network_policy_stored':True,'oversized_pod_rejected':True,
                'quota_initialized_after_seconds':quota_wait_seconds,
                'ephemeral_storage_limit_and_default_request':'512Mi','nodes':0,
                'runtime_or_cni_or_pids_or_disk_verified':False,'release_compatibility_matrix_verified':False}
            (ROOT/'kata-profile-verification.json').write_text(json.dumps(report,indent=2)+'\n')
            print(json.dumps(report),flush=True)
        finally:
            # No nodes exist, so these are only never-executed fixture objects.
            if created_workload:
                custom.delete_namespaced_custom_object('sandbox.opensandbox.io','v1alpha1',name,'batchsandboxes',created_workload['metadata']['name'],
                    body={'preconditions':{'uid':created_workload['metadata']['uid']}})
            if created_pod:
                latest=core.read_namespaced_pod(created_pod.metadata.name,name)
                assert latest.metadata.uid==created_pod.metadata.uid and not latest.spec.node_name
                core.delete_namespaced_pod(latest.metadata.name,name,body=client.V1DeleteOptions(preconditions=client.V1Preconditions(uid=latest.metadata.uid)))
                latest=core.read_namespaced_pod(latest.metadata.name,name)
                core.patch_namespaced_pod(latest.metadata.name,name,[{'op':'test','path':'/metadata/uid','value':latest.metadata.uid},
                    {'op':'test','path':'/metadata/resourceVersion','value':latest.metadata.resource_version},
                    {'op':'replace','path':'/metadata/finalizers','value':[]}],_content_type='application/json-patch+json')
            if created_workload:
                latest=custom.get_namespaced_custom_object('sandbox.opensandbox.io','v1alpha1',name,'batchsandboxes',created_workload['metadata']['name'])
                custom.patch_namespaced_custom_object('sandbox.opensandbox.io','v1alpha1',name,'batchsandboxes',latest['metadata']['name'],
                    {'metadata':{'uid':latest['metadata']['uid'],'resourceVersion':latest['metadata']['resourceVersion'],'finalizers':[]}})
            if created_namespace:core.delete_namespace(name,body=client.V1DeleteOptions(preconditions=client.V1Preconditions(uid=created_namespace.metadata.uid)))
            if created_runtime:runtime.delete_runtime_class(created_runtime.metadata.name,body=client.V1DeleteOptions(preconditions=client.V1Preconditions(uid=created_runtime.metadata.uid)))
            if created_crd:crds.delete_custom_resource_definition(created_crd.metadata.name,body=client.V1DeleteOptions(preconditions=client.V1Preconditions(uid=created_crd.metadata.uid)))


if __name__=='__main__':main()
