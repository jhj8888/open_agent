"""Official SDK wire contracts with synthetic API responses, not real kubelet evidence."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import urlsplit
from uuid import uuid4

from kubernetes import client
import pytest
from urllib3 import HTTPResponse
from urllib3.util.retry import Retry

from agent.durable.cluster_control import ClusterSnapshot
from agent.durable.cluster_lifecycle import ClusterLifecycle
from agent.durable.cluster_process import ClusterProcessError, ClusterProgram, FINALIZER, SCHEDULING_GATE, KubernetesProcesses
from agent.tests.test_cluster_control import snapshot


class SyntheticAPI:
    """Interpose below the official serializer/deserializer. All status is synthetic."""
    def __init__(self, target):
        self.target, self.calls, self.pod = target, [], None
        self.node_uid = str(uuid4())
        self.node_ready = True
        self.namespace_uid = target.namespaceUid
        self.fail_create = False
        self.conflict_patch = False
        self.version = 1

    def request(self, method, url, **kwargs):
        path = urlsplit(url).path
        body = json.loads(kwargs['body']) if kwargs.get('body') else None
        self.calls.append((method, path, body, kwargs.get('headers', {})))
        status = 200
        if path.endswith('/namespaces/kube-system'):
            result = {'metadata': {'name': 'kube-system', 'uid': self.target.clusterId}, 'status': {'phase': 'Active'}}
        elif path.endswith('/namespaces/' + self.target.namespace):
            result = {'metadata': {'name': self.target.namespace, 'uid': self.namespace_uid}, 'status': {'phase': 'Active'}}
        elif '/nodes/' in path:
            result = {'metadata': {'name': 'cpu-node-1', 'uid': self.node_uid},
                      'status': {'conditions': [{'type': 'Ready', 'status': 'True' if self.node_ready else 'Unknown'}]}}
        elif method == 'POST':
            self.pod = deepcopy(body)
            self.pod['metadata'].update(uid=str(uuid4()), resourceVersion='1')
            self.pod['spec'].update(priority=0, preemptionPolicy='PreemptLowerPriority', serviceAccount=body['spec']['serviceAccountName'])
            self.pod['spec']['tolerations'] = [{'key': key, 'operator': 'Exists', 'effect': 'NoExecute', 'tolerationSeconds': 300}
                for key in ['node.kubernetes.io/not-ready', 'node.kubernetes.io/unreachable']]
            self.pod['status'] = {'phase': 'Pending'}
            if self.fail_create:
                raise OSError('private transport message must not escape')
            result = self.pod
        elif self.pod is None:
            result, status = {'kind': 'Status', 'reason': 'NotFound', 'code': 404}, 404
        elif method == 'DELETE':
            assert body['gracePeriodSeconds'] == 30
            if body['preconditions'] != {'uid': self.pod['metadata']['uid'], 'resourceVersion': self.pod['metadata']['resourceVersion']}:
                result, status = {'reason': 'Conflict', 'code': 409}, 409
            else:
                self.pod['metadata']['deletionTimestamp'] = '2026-11-05T01:00:02Z'
                self.bump()
                result = {'kind': 'Status', 'status': 'Success', 'code': 200}
        elif method == 'PATCH':
            assert kwargs['headers']['Content-Type'] == 'application/json-patch+json'
            expected = [{'op': 'test', 'path': '/metadata/uid', 'value': self.pod['metadata']['uid']},
                        {'op': 'test', 'path': '/metadata/resourceVersion', 'value': self.pod['metadata']['resourceVersion']}]
            activation = body[2]['path'] == '/spec/schedulingGates'
            expected.append({'op': 'test', 'path': '/spec/schedulingGates' if activation else '/metadata/finalizers',
                             'value': self.pod['spec'].get('schedulingGates') if activation else self.pod['metadata']['finalizers']})
            if self.conflict_patch or body[:3] != expected:
                result, status = {'reason': 'Conflict', 'code': 409}, 409
            else:
                if activation:
                    assert body[3] == {'op': 'remove', 'path': '/spec/schedulingGates'}
                    self.pod['spec'].pop('schedulingGates')
                else:
                    self.pod['metadata']['finalizers'] = body[3]['value']
                result = deepcopy(self.pod)
                if not self.pod['metadata']['finalizers'] and 'deletionTimestamp' in self.pod['metadata']:
                    self.pod = None
        else:
            result = self.pod
        return HTTPResponse(body=json.dumps(result).encode(), status=status, headers={'Content-Type': 'application/json'})

    def bump(self):
        self.version += 1
        self.pod['metadata']['resourceVersion'] = str(self.version)

    def terminated(self):
        self.pod['spec']['nodeName'] = 'cpu-node-1'
        cid = 'containerd://' + 'a' * 64
        self.pod['status'] = {'phase': 'Succeeded', 'containerStatuses': [{
            'name': 'executor', 'ready': False, 'restartCount': 0, 'image': self.target.executorImage,
            'imageID': 'sha256:' + 'a' * 64, 'containerID': cid, 'state': {'terminated': {
                'containerID': cid, 'exitCode': 0, 'startedAt': '2026-11-05T01:00:00Z', 'finishedAt': '2026-11-05T01:00:01Z'}}}]}
        self.bump()


@pytest.fixture
def system(monkeypatch):
    row = ClusterSnapshot.model_validate(snapshot()).model_copy(update={'phase': 'launching'})
    configuration = client.Configuration()
    configuration.host = 'https://cluster.invalid'
    configuration.retries = Retry(total=0, connect=0, read=0, redirect=0, status=0)
    api_client = client.ApiClient(configuration)
    driver = KubernetesProcesses(api_client, row.target, ClusterProgram('executor-worker', 'executor', 'trusted'))
    server = SyntheticAPI(row.target)
    monkeypatch.setattr(api_client.rest_client.pool_manager, 'request', server.request)
    yield row, driver, server
    api_client.close()


async def started(system, *, bound=True):
    row, driver, server = system
    pod = await driver.launch(row)
    row = row.model_copy(update={'podUid': pod.metadata.uid, 'observedResourceVersion': pod.metadata.resource_version,
                                 'phase': 'observed'})
    await driver.activate(row)
    if bound:
        server.pod['spec']['nodeName'] = 'cpu-node-1'
        server.pod['status']['phase'] = 'Running'
    row = row.model_copy(update={'state': 'failed', 'leaseState': 'cleanup_pending'})
    return row, driver, server


@pytest.mark.asyncio
async def test_sdk_manifest_has_no_token_host_resources_or_credentials(system):
    row, driver, server = await started(system)
    spec = server.pod['spec']
    assert spec['restartPolicy'] == 'Never' and spec['automountServiceAccountToken'] is False
    assert driver.manifest(row)['spec']['schedulingGates'] == [{'name': SCHEDULING_GATE}]
    assert not spec.get('schedulingGates')
    assert len(spec['containers']) == 1 and len(spec['volumes']) == 2
    security = spec['containers'][0]['securityContext']
    assert security['runAsNonRoot'] and security['readOnlyRootFilesystem'] and not security['allowPrivilegeEscalation']
    assert security['capabilities']['drop'] == ['ALL']
    assert json.loads(spec['containers'][0]['env'][0]['value']) == row.lease.model_dump()
    assert not {'hostPath', 'projected', 'persistentVolumeClaim'} & set(spec['volumes'][0])
    assert len([call for call in server.calls if call[0] == 'POST']) == 1


@pytest.mark.asyncio
async def test_activation_requires_recorded_uid_and_preserves_finalizer(system):
    row, driver, server = system
    pod = await driver.launch(row)
    with pytest.raises(ClusterProcessError, match='CLUSTER_ACTIVATION_UNAUTHORIZED'):
        await driver.activate(row)
    row = row.model_copy(update={'phase': 'observed', 'podUid': pod.metadata.uid, 'observedResourceVersion': pod.metadata.resource_version})
    await driver.activate(row)
    assert 'schedulingGates' not in server.pod['spec'] and FINALIZER in server.pod['metadata']['finalizers']
    await driver.activate(row)
    assert len([call for call in server.calls if call[0] == 'PATCH']) == 1


@pytest.mark.asyncio
async def test_lost_uid_commit_never_ungates_execution(system):
    row, driver, server = system
    ledger = SimpleNamespace(target=row.target, begin=AsyncMock(return_value=row),
                             observed=AsyncMock(side_effect=RuntimeError('lost commit')))
    with pytest.raises(RuntimeError, match='lost commit'):
        await ClusterLifecycle(ledger, driver).launch(row)
    assert server.pod['spec']['schedulingGates'] == [{'name': SCHEDULING_GATE}]
    assert not any(call[0] == 'PATCH' for call in server.calls)


@pytest.mark.asyncio
async def test_lost_create_reply_is_recovered_without_another_create(system):
    row, driver, server = system
    server.fail_create = True
    with pytest.raises(ClusterProcessError, match='^CLUSTER_OPERATION_UNCONFIRMED$'):
        await driver.launch(row)
    pod = await driver.inspect(row)
    assert pod.metadata.uid == server.pod['metadata']['uid']
    with pytest.raises(ClusterProcessError, match='CLUSTER_LAUNCH_CONFLICT'):
        await driver.launch(row)
    assert len([call for call in server.calls if call[0] == 'POST']) == 1


@pytest.mark.asyncio
async def test_changed_namespace_uid_stops_before_any_create(system):
    row, driver, server = system
    server.namespace_uid = str(uuid4())
    with pytest.raises(ClusterProcessError, match='CLUSTER_TARGET_CHANGED'):
        await driver.launch(row)
    assert all(call[0] == 'GET' for call in server.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['uid', 'sidecar', 'token', 'host', 'image', 'command', 'env', 'privilege', 'owner', 'annotation', 'priority'])
async def test_replacement_and_admission_injections_are_not_deleted(system, change):
    row, driver, server = await started(system)
    server.calls.clear()
    pod = server.pod
    if change == 'uid': pod['metadata']['uid'] = str(uuid4())
    if change == 'sidecar': pod['spec']['containers'].append({'name': 'extra', 'image': 'bad'})
    if change == 'token': pod['spec']['automountServiceAccountToken'] = True
    if change == 'host': pod['spec']['volumes'].append({'name': 'host', 'hostPath': {'path': '/'}})
    if change == 'image': pod['spec']['containers'][0]['image'] = 'bad:latest'
    if change == 'command': pod['spec']['containers'][0]['command'] = ['sh']
    if change == 'env': pod['spec']['containers'][0]['env'].append({'name': 'INJECT', 'value': 'bad'})
    if change == 'privilege': pod['spec']['containers'][0]['securityContext']['privileged'] = True
    if change == 'owner': pod['metadata']['ownerReferences'] = [{'apiVersion': 'v1', 'kind': 'Pod', 'name': 'other', 'uid': str(uuid4())}]
    if change == 'annotation': pod['metadata']['annotations'].clear()
    if change == 'priority': pod['spec']['priority'] = 1000000
    with pytest.raises(ClusterProcessError, match='CLUSTER_POD_IDENTITY_MISMATCH'):
        await driver.inspect(row)
    assert all(call[0] not in {'DELETE', 'PATCH'} for call in server.calls)


@pytest.mark.asyncio
async def test_delete_and_finalizer_patch_use_uid_resource_version_and_keep_other_finalizers(system):
    row, driver, server = await started(system)
    server.terminated()
    server.pod['metadata']['finalizers'].append('other.example/retain')
    pod = await driver.inspect(row)
    await driver.request_stop(row, pod)
    pod = await driver.inspect(row)
    receipt = await driver.termination(row, pod)
    row = row.model_copy(update={'phase': 'terminated', 'termination': receipt})
    await driver.finalize(row)
    assert server.pod['metadata']['finalizers'] == ['other.example/retain']
    assert not await driver.absent_after_receipt(row)
    await driver.finalize(row)  # Retrying after our finalizer disappeared does not touch others.
    assert len([call for call in server.calls if call[0] == 'PATCH' and call[2][2]['path'] == '/metadata/finalizers']) == 1


@pytest.mark.asyncio
async def test_finalizer_cannot_be_removed_before_receipt_and_conflict_preserves_it(system):
    row, driver, server = await started(system)
    server.terminated()
    with pytest.raises(ClusterProcessError, match='CLUSTER_RECEIPT_NOT_PERSISTED'):
        await driver.finalize(row)
    await driver.request_stop(row, await driver.inspect(row))
    receipt = await driver.termination(row, await driver.inspect(row))
    row = row.model_copy(update={'phase': 'terminated', 'termination': receipt})
    server.conflict_patch = True
    with pytest.raises(ClusterProcessError, match='CLUSTER_OPERATION_UNCONFIRMED'):
        await driver.finalize(row)
    assert FINALIZER in server.pod['metadata']['finalizers']
    server.conflict_patch = False
    await driver.finalize(row)
    assert await driver.absent_after_receipt(row)


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['missing', 'lost_node', 'node_not_ready', 'no_exit', 'restarted', 'different_container', 'bad_time'])
async def test_uncertain_termination_never_becomes_receipt(system, change):
    row, driver, server = await started(system)
    server.calls.clear()
    server.terminated()
    status = server.pod['status']
    if change == 'missing':
        server.pod = None
        assert await driver.inspect(row) is None
        with pytest.raises(ClusterProcessError, match='CLUSTER_RECEIPT_NOT_PERSISTED'):
            await driver.absent_after_receipt(row)
        return
    if change == 'lost_node': status.update(phase='Failed', containerStatuses=[])
    if change == 'node_not_ready': server.node_ready = False
    if change == 'no_exit': status['containerStatuses'][0]['state'] = {'running': {'startedAt': '2026-11-05T01:00:00Z'}}
    if change == 'restarted': status['containerStatuses'][0]['restartCount'] = 1
    if change == 'different_container': status['containerStatuses'][0]['containerID'] = 'containerd://other'
    if change == 'bad_time': status['containerStatuses'][0]['state']['terminated']['finishedAt'] = '2026-11-05T00:00:00Z'
    with pytest.raises(ClusterProcessError, match='CLUSTER_(TERMINATION|NODE)_UNCONFIRMED'):
        await driver.termination(row, await driver.inspect(row))
    assert not any(call[0] == 'PATCH' for call in server.calls)


class Ledger:
    """Only the already-tested HTTP ledger is substituted in lifecycle tests."""
    def __init__(self, row):
        self.row, self.target = row, row.target
        self.fail_receipt = False

    async def inspect(self, lease):
        assert lease == self.row.lease.leaseId
        return self.row

    async def fence(self, lease):
        self.row = self.row.model_copy(update={'state': 'failed', 'leaseState': 'cleanup_pending'})
        return self.row

    async def observed(self, lease, uid, version):
        self.row = self.row.model_copy(update={'phase': 'observed', 'podUid': uid, 'observedResourceVersion': version})
        return self.row

    async def terminated(self, lease, receipt):
        if self.fail_receipt: raise RuntimeError('ledger unavailable')
        self.row = self.row.model_copy(update={'phase': 'terminated', 'termination': receipt})
        return self.row

    async def not_started(self, lease, receipt):
        if self.fail_receipt: raise RuntimeError('ledger unavailable')
        self.row = self.row.model_copy(update={'phase': 'not_started', 'notStarted': receipt})
        return self.row


@pytest.mark.asyncio
async def test_recovery_survives_crash_before_receipt_commit(system):
    row, driver, server = await started(system)
    ledger = Ledger(row)
    lifecycle = ClusterLifecycle(ledger, driver)
    row, absent = await lifecycle.stop_once(row)
    assert row.phase == 'observed' and not absent
    server.terminated()
    ledger.fail_receipt = True
    with pytest.raises(RuntimeError, match='ledger unavailable'):
        await lifecycle.stop_once(row)
    assert FINALIZER in server.pod['metadata']['finalizers']
    ledger.fail_receipt = False
    row, absent = await lifecycle.stop_once(row)
    assert row.phase == 'terminated' and absent
    # Another process can finish cleanup using only the persisted receipt.
    row, absent = await ClusterLifecycle(ledger, driver).stop_once(row)
    assert absent and row.termination is not None
    assert len([call for call in server.calls if call[0] == 'POST']) == 1


@pytest.mark.asyncio
async def test_recovery_before_uid_commit_binds_but_never_activates_gated_pod(system):
    row, driver, server = system
    await driver.launch(row)
    ledger = Ledger(row)
    row, absent = await ClusterLifecycle(ledger, driver).stop_once(row)
    assert row.phase == 'observed' and not absent
    assert server.pod['spec']['schedulingGates'] == [{'name': SCHEDULING_GATE}]
    assert not any(call[0] == 'PATCH' for call in server.calls)


@pytest.mark.asyncio
async def test_unstarted_pod_delete_barrier_is_persisted_before_finalizer_removal(system):
    row, driver, server = system
    await driver.launch(row)
    ledger = Ledger(row)
    lifecycle = ClusterLifecycle(ledger, driver)
    row, absent = await lifecycle.stop_once(row)
    assert not absent and row.phase == 'observed'
    assert server.pod['metadata']['deletionTimestamp']
    ledger.fail_receipt = True
    with pytest.raises(RuntimeError, match='ledger unavailable'):
        await lifecycle.stop_once(row)
    assert FINALIZER in server.pod['metadata']['finalizers']
    with pytest.raises(ClusterProcessError, match='CLUSTER_ACTIVATION_UNCONFIRMED'):
        await driver.activate(row.model_copy(update={'state': 'running', 'leaseState': 'held'}))
    ledger.fail_receipt = False
    row, absent = await lifecycle.stop_once(row)
    assert absent and row.phase == 'not_started' and row.termination is None
    assert row.notStarted.nodeUnbound and row.notStarted.containerStatusesEmpty
    assert (await ClusterLifecycle(ledger, driver).stop_once(row))[1]
    assert len([call for call in server.calls if call[0] == 'POST']) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['no_delete', 'bound_node', 'container_status', 'running'])
async def test_unstarted_receipt_requires_unbound_state_and_deletion_barrier(system, change):
    row, driver, server = system
    pod = await driver.launch(row)
    row = row.model_copy(update={'phase': 'observed', 'podUid': pod.metadata.uid, 'observedResourceVersion': '1',
                                 'state': 'failed', 'leaseState': 'cleanup_pending'})
    if change != 'no_delete': await driver.request_stop(row, pod)
    if change == 'bound_node': server.pod['spec']['nodeName'] = 'cpu-node-1'
    if change == 'running': server.pod['status']['phase'] = 'Running'
    if change == 'container_status':
        server.terminated()
        server.pod['spec'].pop('nodeName')
    with pytest.raises(ClusterProcessError, match='CLUSTER_(NOT_STARTED_UNCONFIRMED|POD_IDENTITY_MISMATCH)'):
        driver.not_started_receipt(row, await driver.inspect(row))
    assert not any(call[0] == 'PATCH' for call in server.calls)


@pytest.mark.asyncio
async def test_activated_but_never_bound_pod_has_same_positive_delete_barrier(system):
    row, driver, server = await started(system, bound=False)
    ledger = Ledger(row)
    lifecycle = ClusterLifecycle(ledger, driver)
    row, absent = await lifecycle.stop_once(row)
    assert not absent and row.phase == 'observed'
    row, absent = await lifecycle.stop_once(row)
    assert absent and row.phase == 'not_started' and row.notStarted.schedulingGate is None


@pytest.mark.asyncio
async def test_unknown_create_missing_is_not_same_as_never_authorized(system):
    row, driver, server = system
    ledger = Ledger(row)
    with pytest.raises(ClusterProcessError, match='CLUSTER_LAUNCH_UNCONFIRMED'):
        await ClusterLifecycle(ledger, driver).stop_once(row)
    ledger.row = row.model_copy(update={'phase': 'prepared'})
    row, absent = await ClusterLifecycle(ledger, driver).stop_once(ledger.row)
    assert absent and row.phase == 'prepared'
    assert all(call[0] == 'GET' for call in server.calls)


@pytest.mark.asyncio
async def test_lost_begin_does_not_call_create(system):
    row, driver, server = system
    ledger = SimpleNamespace(target=row.target, begin=AsyncMock(side_effect=RuntimeError('lost begin')))
    with pytest.raises(RuntimeError, match='lost begin'):
        await ClusterLifecycle(ledger, driver).launch(row)
    assert server.calls == []


@pytest.mark.parametrize('field,value', [('host', 'http://cluster.invalid'), ('verify_ssl', False),
    ('assert_hostname', False), ('debug', True), ('proxy', 'http://proxy.invalid'), ('retries', 3)])
def test_transport_rejects_unsafe_tls_debug_proxy_or_retry(system, field, value):
    row, driver, server = system
    setattr(driver.api_client.configuration, field, value)
    with pytest.raises(ClusterProcessError, match='CLUSTER_TRANSPORT_INVALID'):
        KubernetesProcesses(driver.api_client, row.target, driver.program)


@pytest.mark.parametrize('secret,account,pool', [(None, 'executor', 'trusted'), ('worker', 'default', 'trusted'),
                                              ('../worker', 'executor', 'trusted'), ('worker', 'executor', None)])
def test_program_requires_valid_operator_names(secret, account, pool):
    with pytest.raises(ClusterProcessError, match='CLUSTER_PROGRAM_INVALID'):
        ClusterProgram(secret, account, pool)


@pytest.mark.parametrize('names', [('bad/name',), ('registry', 'registry'), tuple('abcde'), 'registry', (None,)])
def test_registry_secret_names_are_bounded_operator_references(names):
    with pytest.raises(ClusterProcessError, match='CLUSTER_PROGRAM_INVALID'):
        ClusterProgram('worker', 'executor', 'trusted', image_pull_secrets=names)


@pytest.mark.asyncio
async def test_private_registry_references_are_explicit_and_tamper_checked(system):
    row, driver, server = system
    plain = driver.manifest(row)
    driver.program = ClusterProgram('executor-worker', 'executor', 'trusted',
                                    image_pull_secrets=('registry-primary', 'registry-secondary'))
    pod = await driver.launch(row)
    assert [item.name for item in pod.spec.image_pull_secrets] == ['registry-primary', 'registry-secondary']
    assert pod.metadata.annotations['open-agent.io/binding-sha256'] != plain['metadata']['annotations']['open-agent.io/binding-sha256']
    assert all('/secrets' not in request[1] for request in server.calls)
    pod.spec.image_pull_secrets.reverse()
    with pytest.raises(ClusterProcessError, match='CLUSTER_POD_IDENTITY_MISMATCH'):
        driver.validate_pod(row, pod)


@pytest.mark.asyncio
async def test_implicit_registry_secret_injection_is_rejected(system):
    row, driver, _ = system
    pod = await driver.launch(row)
    pod.spec.image_pull_secrets = [client.V1LocalObjectReference(name='unexpected-registry')]
    with pytest.raises(ClusterProcessError, match='CLUSTER_POD_IDENTITY_MISMATCH'):
        driver.validate_pod(row, pod)
