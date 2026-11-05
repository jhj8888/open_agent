import copy
import json
from uuid import uuid4

import httpx
import pytest

from agent.durable.cluster_control import ClusterCleanupProof, ClusterSupervisorClient, ClusterTarget, PodTermination, PodNotStarted, PodCreationRejection
from agent.durable.contracts import RunRef
from agent.durable.control import ControlPlaneClient, ControlPlaneError


def snapshot():
    target = {'workerId': 'executor', 'environment': 'test', 'clusterId': str(uuid4()),
              'namespace': 'executors-test', 'namespaceUid': str(uuid4()),
              'executorImage': 'registry.invalid/worker@sha256:' + 'a' * 64}
    lease = {'scope': {'tenant': 'tenant', 'environment': 'test'}, 'runId': str(uuid4()),
             'attempt': 1, 'leaseId': str(uuid4()), 'fencingToken': 1, 'workerId': 'executor'}
    return {'target': target, 'lease': lease, 'supervisorId': 'supervisor',
            'podName': 'oa-exec-' + lease['leaseId'].replace('-', ''), 'podUid': None,
            'observedResourceVersion': None, 'phase': 'prepared', 'state': 'running', 'leaseState': 'held',
            'heartbeatUntil': '2026-11-05T01:00:00Z', 'deadlineAt': '2026-11-05T02:00:00Z',
            'sandboxPhase': None, 'sandboxId': None, 'isolationProfile': None, 'runtimeImage': None, 'termination': None}


def terminated(row):
    row.update(phase='terminated', state='succeeded', leaseState='cleanup_pending',
               podUid=str(uuid4()), observedResourceVersion='100')
    row['termination'] = {'id': str(uuid4()), 'podUid': row['podUid'], 'resourceVersion': '200',
                          'nodeName': 'cpu-node-1', 'nodeUid': str(uuid4()), 'podPhase': 'Succeeded',
                          'containerName': 'executor', 'containerId': 'containerd://' + 'b' * 64, 'exitCode': 0,
                          'startedAt': '2026-11-05T01:00:00Z', 'finishedAt': '2026-11-05T01:00:01Z',
                          'evidenceSha256': 'c' * 64}
    return row


def proof(row):
    return ClusterCleanupProof(proofId=str(uuid4()), kind='terminated_pod' if row['termination'] else 'never_launched',
                               clusterId=row['target']['clusterId'], namespaceUid=row['target']['namespaceUid'],
                               podName=row['podName'], podUid=row['podUid'], sandboxId=None,
                               terminationId=row['termination']['id'] if row['termination'] else None,
                               evidenceSha256='d' * 64, podAbsent=True, sandboxAbsent=True, creationSettled=True)


def client(row, respond):
    control = ControlPlaneClient('https://control.internal', 'supervisor', 's' * 32,
                                 transport=httpx.MockTransport(respond))
    return control, ClusterSupervisorClient(control, 'supervisor', ClusterTarget.model_validate(row['target']))


def rejected():
    row = snapshot()
    row.update(phase='rejected', state='failed', leaseState='cleanup_pending')
    row['creationRejection'] = dict(id=str(uuid4()),auditId=str(uuid4()),podName=row['podName'],
        namespaceUid=row['target']['namespaceUid'],httpStatus=403,reason='Forbidden',requestSha256='a'*64,responseSha256='b'*64)
    return row


@pytest.mark.asyncio
async def test_rejected_creation_receipt_and_reserved_sandbox_release_are_recoverable():
    row = rejected()
    row['sandboxPhase'] = 'reserved'
    paths = []
    def respond(request):
        paths.append(request.url.path)
        if request.url.path.endswith('/release'):
            row.update(phase='released', leaseState='released',sandboxPhase='released')
        return httpx.Response(200,json=row)
    control, api = client(row,respond)
    try:
        receipt = PodCreationRejection.model_validate(row['creationRejection'])
        assert (await api.creation_rejected(row['lease']['leaseId'],receipt)).creationRejection == receipt
        body = proof(row).model_copy(update={'kind':'create_rejected','terminationId':receipt.id})
        assert (await api.release(row['lease']['leaseId'],body)).phase == 'released'
        assert (await api.inspect(row['lease']['leaseId'])).creationRejection == receipt
        assert paths[0].endswith('/creation-rejected')
    finally:
        await control.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize('change',['missing','status','namespace','pod_name','pod_uid','running','held','sandbox_creating','wrong_phase'])
async def test_creation_rejection_snapshots_reject_conflicting_or_ambiguous_state(change):
    row = rejected()
    if change == 'missing': row['creationRejection'] = None
    if change == 'status': row['creationRejection']['httpStatus'] = 409
    if change == 'namespace': row['creationRejection']['namespaceUid'] = str(uuid4())
    if change == 'pod_name': row['creationRejection']['podName'] = 'oa-exec-' + '1'*32
    if change == 'pod_uid': row.update(podUid=str(uuid4()),observedResourceVersion='1')
    if change == 'running': row['state'] = 'running'
    if change == 'held': row['leaseState'] = 'held'
    if change == 'sandbox_creating': row['sandboxPhase'] = 'creating'
    if change == 'wrong_phase': row['phase'] = 'launching'
    control, api = client(row,lambda _: httpx.Response(200,json=row))
    try:
        with pytest.raises(ControlPlaneError,match='CONTROL_CLUSTER_INVALID'):
            await api.inspect(row['lease']['leaseId'])
    finally:
        await control.aclose()


def unstarted():
    row = snapshot()
    row.update(phase='not_started', state='cancelled', leaseState='cleanup_pending', podUid=str(uuid4()), observedResourceVersion='100')
    row['notStarted'] = {'id': str(uuid4()), 'podUid': row['podUid'], 'resourceVersion': '101',
                         'deletionTimestamp': '2026-11-05T01:00:01Z', 'schedulingGate': 'open-agent.io/execution-authorized',
                         'nodeUnbound': True, 'containerStatusesEmpty': True, 'evidenceSha256': 'e' * 64}
    return row


@pytest.mark.asyncio
async def test_unstarted_receipt_and_release_use_matching_durable_identity():
    row = unstarted()
    paths = []
    def respond(request):
        paths.append(request.url.path)
        if request.url.path.endswith('/release'):
            row.update(phase='released', leaseState='released')
        return httpx.Response(200, json=row)
    control, api = client(row, respond)
    try:
        receipt = PodNotStarted.model_validate(row['notStarted'])
        result = await api.not_started(row['lease']['leaseId'], receipt)
        assert result.notStarted == receipt
        body = proof(row).model_copy(update={'kind': 'never_started_pod', 'terminationId': receipt.id})
        assert (await api.release(row['lease']['leaseId'], body)).phase == 'released'
        assert paths[0].endswith('/not-started')
    finally:
        await control.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['missing', 'wrong_uid', 'node_bound', 'statuses', 'running', 'two_receipts'])
async def test_unstarted_responses_reject_contradictory_evidence(change):
    row = unstarted()
    if change == 'missing': row['notStarted'] = None
    if change == 'wrong_uid': row['notStarted']['podUid'] = str(uuid4())
    if change == 'node_bound': row['notStarted']['nodeUnbound'] = False
    if change == 'statuses': row['notStarted']['containerStatusesEmpty'] = False
    if change == 'running': row['state'] = 'running'
    if change == 'two_receipts': row['termination'] = terminated(snapshot())['termination']
    control, api = client(row, lambda request: httpx.Response(200, json=row))
    try:
        with pytest.raises(ControlPlaneError, match='CONTROL_CLUSTER_INVALID'):
            await api.inspect(row['lease']['leaseId'])
    finally:
        await control.aclose()


@pytest.mark.asyncio
async def test_claim_is_bound_to_requested_run_namespace_and_supervisor():
    row = snapshot()
    calls = []
    def respond(request):
        calls.append(request)
        return httpx.Response(200, json={'lease': row['lease'], 'state': 'running', 'execution': row})
    control, api = client(row, respond)
    try:
        run = RunRef('tenant', 'test', row['lease']['runId'])
        value = await api.claim(run)
        assert value.lease.attempt_lease().run == run
        assert calls[0].url.path.endswith('/reconcile/cluster/runs/' + run.run_id + '/claim')
        assert json.loads(calls[0].content) == {'tenant': 'tenant', 'environment': 'test'}
        assert calls[0].headers['X-Platform-Key-Id'] == 'supervisor'
    finally:
        await control.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize('mutation', [
    lambda r: r['target'].update(namespaceUid=str(uuid4())),
    lambda r: r.update(supervisorId='other'),
    lambda r: r['lease'].update(workerId='other'),
    lambda r: r.update(podName='oa-exec-' + 'a' * 32),
    lambda r: r.update(phase='observed'),
    lambda r: r.update(observedResourceVersion='100'),
    lambda r: r.update(prompt='PRIVATE-INPUT-MUST-NOT-APPEAR'),
])
async def test_mismatched_or_private_response_data_fails_closed(mutation):
    row = snapshot()
    response = copy.deepcopy(row)
    mutation(response)
    control, api = client(row, lambda _: httpx.Response(200, json=response))
    try:
        with pytest.raises(ControlPlaneError, match='^CONTROL_CLUSTER_INVALID$'):
            await api.inspect(row['lease']['leaseId'])
    finally:
        await control.aclose()


@pytest.mark.asyncio
async def test_lost_launch_response_is_not_retried_and_wrong_environment_never_contacts_control():
    row = snapshot()
    calls = []
    def respond(request):
        calls.append(request)
        raise httpx.ReadTimeout('private endpoint detail')
    control, api = client(row, respond)
    try:
        with pytest.raises(ControlPlaneError, match='^CONTROL_CLUSTER_INPUT_INVALID$'):
            await api.claim(RunRef('tenant', 'prod', row['lease']['runId']))
        assert not calls
        with pytest.raises(ControlPlaneError, match='^CONTROL_UNAVAILABLE$'):
            await api.begin(row['lease']['leaseId'])
        assert len(calls) == 1
    finally:
        await control.aclose()


@pytest.mark.asyncio
async def test_termination_checkpoint_and_release_require_matching_acknowledgements():
    row = terminated(snapshot())
    calls = []
    def respond(request):
        calls.append(request)
        return httpx.Response(200, json=row)
    control, api = client(row, respond)
    try:
        receipt = PodTermination.model_validate(row['termination'])
        value = await api.terminated(row['lease']['leaseId'], receipt)
        assert value.termination == receipt
        incoming = proof(row)
        with pytest.raises(ControlPlaneError, match='^CONTROL_CLUSTER_INVALID$'):
            await api.release(row['lease']['leaseId'], incoming)  # Ack still says retained.
        row.update(phase='released', leaseState='released')
        assert (await api.release(row['lease']['leaseId'], incoming)).termination == receipt
        row['termination']['id'] = str(uuid4())
        with pytest.raises(ControlPlaneError, match='^CONTROL_CLUSTER_INVALID$'):
            await api.release(row['lease']['leaseId'], incoming)
        assert len(calls) == 4
    finally:
        await control.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize('mutation', [
    lambda r: r['termination'].update(podUid=str(uuid4())),
    lambda r: r['termination'].update(finishedAt='2026-11-05T00:00:00Z'),
    lambda r: r['termination'].update(exitCode=137),
    lambda r: r.update(phase='released', termination=None, leaseState='released'),
])
async def test_termination_and_deletion_are_not_interchangeable(mutation):
    row = terminated(snapshot())
    response = copy.deepcopy(row)
    mutation(response)
    control, api = client(row, lambda _: httpx.Response(200, json=response))
    try:
        with pytest.raises(ControlPlaneError, match='^CONTROL_CLUSTER_INVALID$'):
            await api.inspect(row['lease']['leaseId'])
    finally:
        await control.aclose()


@pytest.mark.asyncio
async def test_incomplete_or_foreign_release_proof_is_rejected_before_transport():
    row = terminated(snapshot())
    calls = []
    control, api = client(row, lambda request: calls.append(request))
    try:
        incoming = proof(row)
        for change in ({'podAbsent': False}, {'creationSettled': False}, {'clusterId': str(uuid4())}, {'terminationId': None}):
            with pytest.raises(ControlPlaneError, match='^CONTROL_CLUSTER_INPUT_INVALID$'):
                await api.release(row['lease']['leaseId'], incoming.model_copy(update=change))
        assert not calls
    finally:
        await control.aclose()


@pytest.mark.asyncio
async def test_pending_scan_rejects_duplicate_rows_released_leases_and_invalid_cursor():
    row = snapshot()
    replies = [[row, row], [{**row, 'phase': 'released'}]]
    calls = []
    def respond(request):
        calls.append(request)
        return httpx.Response(200, json=replies.pop(0))
    control, api = client(row, respond)
    try:
        for _ in range(2):
            with pytest.raises(ControlPlaneError, match='^CONTROL_CLUSTER_INVALID$'):
                await api.pending()
        with pytest.raises(ControlPlaneError):
            await api.pending(after='')
        assert len(calls) == 2
    finally:
        await control.aclose()
