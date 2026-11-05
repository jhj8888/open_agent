"""Actual API objects and defaults; agentless fixture, no Kata/CNI execution claim."""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import time
from uuid import uuid4

INPUTS = Path(__file__).resolve().parent
ROOT = Path('/opt/open-agent/k3s-api-fixture')
manifest = json.loads((INPUTS / 'cluster-contract-manifest.json').read_bytes())
overlay = Path('/opt/open-agent/cluster-contracts') / manifest['sha256']
sys.path[:0] = [str(overlay / 'source'), str(overlay / 'vendor')]

from kubernetes import client, config
from urllib3.util.retry import Retry
import yaml

from agent.durable.cluster_control import ClusterSnapshot
from agent.durable.cluster_process import ClusterProcessError, ClusterProgram, KubernetesProcesses
from agent.durable.cluster_sandbox import KubernetesSandboxes, KataPolicy, POD_OWNER, POD_INDEX


async def verify(controller=None):
    assert (ROOT / 'owner').read_text() == 'open-agent-k3s-api-fixture-v1'
    cfg = client.Configuration()
    config.load_kube_config(config_file=str(ROOT / 'admin.yaml'), client_configuration=cfg, persist_config=False)
    cfg.retries = Retry(total=0, connect=0, read=0, redirect=0, status=0)
    cfg.debug = False
    with client.ApiClient(cfg) as api:
        core, custom = client.CoreV1Api(api), client.CustomObjectsApi(api)
        network, runtime = client.NetworkingV1Api(api), client.NodeV1Api(api)
        crds = client.ApiextensionsV1Api(api)
        version = client.VersionApi(api).get_code().git_version
        assert version == 'v1.36.6+k3s1' and not core.list_node().items
        raw = yaml.safe_load((INPUTS / 'sandbox.opensandbox.io_batchsandboxes.yaml').read_bytes())
        crds.create_custom_resource_definition(raw, field_validation='Strict')
        for _ in range(60):
            current = crds.read_custom_resource_definition(raw['metadata']['name'])
            if any(c.type == 'Established' and c.status == 'True' for c in current.status.conditions or []): break
            await asyncio.sleep(.5)
        else: raise RuntimeError('fixture_crd_not_established')
        docs = json.loads((INPUTS / 'kata-profile-documents.json').read_bytes())
        workload = json.loads((INPUTS / 'kata-profile-upstream.json').read_bytes())['generated_workload']
        ns = workload['metadata']['namespace']
        namespaces = {}
        for name in ['open-agent-executors-contract', 'open-agent-test', ns]:
            namespaces[name] = core.create_namespace({'metadata': {'name': name, 'labels': {
                'open-agent.io/environment': 'test', 'open-agent.io/sandbox-boundary': 'kata'}}}, field_validation='Strict')
        core.create_namespaced_service_account(ns, docs['service-account.yaml'], field_validation='Strict')
        network.create_namespaced_network_policy(ns, docs['network-policy.yaml'], field_validation='Strict')
        core.create_namespaced_resource_quota(ns, docs['resource-quota.yaml'], field_validation='Strict')
        required = set(docs['resource-quota.yaml']['spec']['hard'])
        started = time.monotonic()
        for attempt in range(840):
            quota = core.read_namespaced_resource_quota('open-agent-sandbox-limits', ns)
            if required.issubset(quota.status.hard or {}) and required.issubset(quota.status.used or {}): break
            if attempt % 30 == 0: print(json.dumps({'event': 'sandbox_quota_initializing', 'seconds': round(time.monotonic()-started)}), flush=True)
            await asyncio.sleep(.5)
        else: raise RuntimeError('fixture_quota_not_initialized')
        runtime_name = workload['spec']['template']['spec']['runtimeClassName']
        runtime_class = runtime.create_runtime_class({'metadata': {'name': runtime_name}, 'handler': runtime_name}, field_validation='Strict')
        lease_id, run_id, executor_uid = str(uuid4()), str(uuid4()), str(uuid4())
        deadline = datetime.now(timezone.utc) + timedelta(minutes=15)
        row = ClusterSnapshot.model_validate({
            'lease': {'scope': {'tenant': 'sandbox-api-contract', 'environment': 'test'}, 'runId': run_id,
                'attempt': 1, 'leaseId': lease_id, 'fencingToken': 1, 'workerId': 'executor-contract'},
            'supervisorId': 'supervisor-contract', 'target': {'workerId': 'executor-contract', 'environment': 'test',
                'clusterId': core.read_namespace('kube-system').metadata.uid, 'namespace': 'open-agent-executors-contract',
                'namespaceUid': namespaces['open-agent-executors-contract'].metadata.uid,
                'executorImage': 'registry.internal/open-agent/worker@sha256:' + 'a'*64},
            'podName': 'oa-exec-' + lease_id.replace('-', ''), 'podUid': executor_uid, 'observedResourceVersion': '1',
            'phase': 'observed', 'state': 'running', 'leaseState': 'held', 'heartbeatUntil': deadline, 'deadlineAt': deadline,
            'sandboxPhase': 'creating', 'sandboxId': None, 'isolationProfile': 'kata',
            'runtimeImage': workload['spec']['template']['spec']['containers'][0]['image'], 'termination': None,
            'sandboxTarget': {'namespace': ns, 'namespaceUid': namespaces[ns].metadata.uid, 'runtimeClass': runtime_name}})
        policy = KataPolicy(controlNamespace='open-agent-test', controlNamespaceUid=namespaces['open-agent-test'].metadata.uid,
            runtimeClassUid=runtime_class.metadata.uid, runtimeHandler=runtime_name, imagePullSecret='open-agent-registry',
            execdImage=workload['spec']['template']['spec']['initContainers'][0]['image'])
        driver = KubernetesSandboxes(KubernetesProcesses(api, row.target, ClusterProgram('worker', 'executor', 'trusted')), row.sandboxTarget, policy)
        sandbox_id = workload['metadata']['name']
        labels = driver._labels(row, sandbox_id)
        workload['metadata']['labels'] = labels
        template = workload['spec']['template']
        template['metadata']['labels'] = deepcopy(labels)
        template['spec']['containers'][0]['env'][0]['value'] = 'synthetic_' + 'x'*54
        workload['spec']['expireTime'] = deadline.isoformat()
        if controller is not None:
            await controller.start(api, 'open-agent-test', INPUTS)
        cr = custom.create_namespaced_custom_object('sandbox.opensandbox.io', 'v1alpha1', ns, 'batchsandboxes', workload, field_validation='Strict')
        pod = {'apiVersion': 'v1', 'kind': 'Pod', **deepcopy(template)}
        pod['metadata'].update(name=sandbox_id+'-0', namespace=ns, labels={**labels, POD_OWNER: sandbox_id, POD_INDEX: '0'},
            ownerReferences=[{'apiVersion': 'sandbox.opensandbox.io/v1alpha1', 'kind': 'BatchSandbox',
                'name': sandbox_id, 'uid': cr['metadata']['uid'], 'controller': True, 'blockOwnerDeletion': True}])
        if controller is None:
            created = core.create_namespaced_pod(ns, pod, field_validation='Strict')
        else:
            for _ in range(120):
                pods = core.list_namespaced_pod(ns).items
                cr = custom.get_namespaced_custom_object('sandbox.opensandbox.io', 'v1alpha1', ns, 'batchsandboxes', sandbox_id)
                if len(pods) == 1 and cr.get('status', {}).get('observedGeneration') == cr['metadata']['generation']:
                    created = pods[0]
                    break
                await asyncio.sleep(.5)
            else: raise RuntimeError('actual_controller_did_not_create_pod')
        assert not created.spec.node_name
        # Save only fixture data (synthetic execd value), privately for diagnosing defaults.
        (ROOT / 'sandbox-resource-fixture.json').write_text(json.dumps({'workload': cr,
            'pod': api.sanitize_for_serialization(created)}, indent=2))
        first = await driver.observe(row, sandbox_id)
        bound = row.model_copy(update={'sandboxId': sandbox_id, 'sandboxResource': first, 'sandboxPhase': 'created'})
        assert await driver.observe(bound, sandbox_id) == first
        rejected = []
        async def reject(name):
            try: await driver.observe(bound, sandbox_id)
            except ClusterProcessError: rejected.append(name)
            else: raise RuntimeError('unsafe_actual_api_resource_was_accepted')
        network.patch_namespaced_network_policy('open-agent-sandbox-boundary', ns, {'spec': {'egress': [{}]}})
        await reject('egress_allow_rule')
        network.patch_namespaced_network_policy('open-agent-sandbox-boundary', ns, {'spec': {'egress': []}})
        extra = deepcopy(docs['network-policy.yaml']); extra['metadata']['name'] = 'unexpected-extra-policy'
        extra_policy = network.create_namespaced_network_policy(ns, extra)
        await reject('additional_network_policy')
        network.delete_namespaced_network_policy(extra_policy.metadata.name, ns,
            body=client.V1DeleteOptions(preconditions=client.V1Preconditions(uid=extra_policy.metadata.uid)))
        core.patch_namespaced_pod(created.metadata.name, ns, {'metadata': {'labels': {'oa.lease': str(uuid4())}}})
        await reject('changed_lease_label')
        core.patch_namespaced_pod(created.metadata.name, ns, {'metadata': {'labels': {'oa.lease': lease_id}}})
        duplicate = deepcopy(pod); duplicate['metadata']['name'] = sandbox_id+'-extra'; duplicate['metadata']['labels'] = {}
        duplicate['metadata']['finalizers'] = []
        extra_pod = core.create_namespaced_pod(ns, duplicate)
        await reject('unlabeled_duplicate_owner_pod')
        core.delete_namespaced_pod(extra_pod.metadata.name, ns, body=client.V1DeleteOptions(
            preconditions=client.V1Preconditions(uid=extra_pod.metadata.uid)))
        for _ in range(40):
            if all(p.metadata.uid != extra_pod.metadata.uid for p in core.list_namespaced_pod(ns).items): break
            await asyncio.sleep(.5)
        else: raise RuntimeError('fixture_duplicate_pod_not_removed')
        assert await driver.observe(bound, sandbox_id) == first
        settlement = await controller.settlement(api, driver, bound) if controller is not None else None
        report = {'checked_at': datetime.now(timezone.utc).isoformat(), 'kubernetes_version': version,
            'source_archive_sha256': manifest['sha256'], 'actual_api_resource_validation': True,
            'actual_uid_resource_version_receipt': first.model_dump(), 'repeated_receipt_preserved': True,
            'rejected_mutations': rejected, 'quota_initialization_seconds': round(time.monotonic()-started, 2),
            'agentless': True, 'executor_lease': 'synthetic; no business DB or executor process',
            'pod_creation': 'fixed official controller' if controller is not None else 'fixture manually mirrors pinned controller labels and ownerReference',
            'controller_executed': controller is not None, 'controller': controller.evidence if controller is not None else None,
            'controller_settlement': settlement, 'tenant_code_executed': False, 'kata_and_cni_verified': False,
            'task_admission_enabled': False, 'supported_upstream_kubernetes_matrix_verified': False}
        (ROOT / 'sandbox-resource-verification.json').write_text(json.dumps(report, indent=2)+'\n')
        print(json.dumps(report), flush=True)


if __name__ == '__main__':
    asyncio.run(verify())
