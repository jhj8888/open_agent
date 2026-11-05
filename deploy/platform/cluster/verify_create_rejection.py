"""Real agentless K3s rejection responses; the lifecycle ledger is synthetic.

Uses only a new owned namespace in the disposable API fixture. No tenant task,
model, kubelet, CNI or sandbox is executed. Response bodies and tokens stay private.
"""
import asyncio
from copy import copy
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import sys
from uuid import uuid4

ROOT = Path('/opt/open-agent/k3s-api-fixture')
INPUTS = Path(__file__).resolve().parent
manifest = json.loads((INPUTS / 'cluster-contract-manifest.json').read_bytes())
overlay = Path('/opt/open-agent/cluster-contracts') / manifest['sha256']
sys.path[:0] = [str(overlay / 'source'), str(overlay / 'vendor')]

from kubernetes import client, config
from urllib3.util.retry import Retry
from agent.durable.cluster_control import ClusterSnapshot
from agent.durable.cluster_lifecycle import ClusterLifecycle
from agent.durable.cluster_process import ClusterCreateRejected, ClusterProgram, KubernetesProcesses, canonical
from agent.durable.cluster_supervisor import ClusterSupervisor
from agent.tests.test_cluster_control import snapshot
from agent.tests.test_cluster_supervisor import Ledger
from verify_execution_chart import service_client


async def main():
    os.umask(0o077)
    assert (ROOT / 'owner').read_text() == 'open-agent-k3s-api-fixture-v1'
    cfg = client.Configuration()
    config.load_kube_config(config_file=str(ROOT / 'admin.yaml'), client_configuration=cfg, persist_config=False)
    cfg.retries = Retry(total=0, connect=0, read=0, redirect=0, status=0)
    cfg.debug = False
    admin = client.ApiClient(cfg)
    core, rbac = client.CoreV1Api(admin), client.RbacAuthorizationV1Api(admin)
    assert client.VersionApi(admin).get_code().git_version == 'v1.36.6+k3s1'
    assert not core.list_node().items
    name = 'oa-create-rejection-' + uuid4().hex[:12]
    namespace, role, binding, clients = None, None, None, []
    evidence = {'overlay_sha256':manifest['sha256'], 'version':'v1.36.6+k3s1',
                'business_database_used':False,'model_or_tool_executed':False,'kubelet_or_cni_or_kata_verified':False}
    try:
        namespace = core.create_namespace({'metadata':{'name':name,'labels':{'open-agent.io/fixture':'create-rejection'}}})
        core.create_namespaced_service_account(name, {'metadata':{'name':'reader'}})
        core.create_namespaced_service_account(name, {'metadata':{'name':'executor'}})
        role = rbac.create_cluster_role({'metadata':{'name':name},'rules':[
            {'apiGroups':[''],'resources':['namespaces'],'verbs':['get'],'resourceNames':[name,'kube-system']},
            {'apiGroups':[''],'resources':['pods'],'verbs':['get']}]})
        binding = rbac.create_cluster_role_binding({'metadata':{'name':name},
            'roleRef':{'apiGroup':'rbac.authorization.k8s.io','kind':'ClusterRole','name':name},
            'subjects':[{'kind':'ServiceAccount','name':'reader','namespace':name}]})
        limited = service_client(admin,name,'reader')
        clients.append(limited)
        data = snapshot()
        data['target'].update(namespace=name,namespaceUid=namespace.metadata.uid,
                              clusterId=core.read_namespace('kube-system').metadata.uid)
        row = ClusterSnapshot.model_validate(data)
        program = ClusterProgram('fixture-worker','executor','trusted')
        driver = KubernetesProcesses(limited,row.target,program)
        ledger = Ledger(row)
        supervisor = ClusterSupervisor(ledger,ClusterLifecycle(ledger,driver))
        assert (await supervisor._execute(row.lease.attempt_lease().run)).state == 'failed'
        receipt = ledger.row.creationRejection
        assert receipt and receipt.httpStatus == 403 and receipt.reason == 'Forbidden'
        assert receipt.requestSha256 == sha256(canonical(driver.manifest(row))).hexdigest()
        assert (await supervisor.cleanup(ledger.row)).phase == 'released'
        assert (await supervisor.cleanup(ledger.row)).phase == 'released'
        assert ledger.events.count('begin') == 1 and len(ledger.proofs) == 1
        evidence['forbidden_lifecycle'] = {'audit_id':receipt.auditId,'receipt_id':receipt.id,
            'state':ledger.row.state,'phase':ledger.row.phase,'one_begin':True,'one_release_proof':True}

        # Malformed configured spec: real core validation rejects before storage.
        class InvalidSpec(KubernetesProcesses):
            def manifest(self, current):
                body = super().manifest(current)
                body['spec']['restartPolicy'] = 'invalid-fixture-policy'
                return body
        invalid = InvalidSpec(admin,row.target,program)
        launching = row.model_copy(update={'phase':'launching'})
        try:
            await invalid.launch(launching)
        except ClusterCreateRejected as error:
            assert error.receipt.httpStatus == 422 and error.receipt.reason == 'Invalid'
            assert error.receipt.requestSha256 == sha256(canonical(invalid.manifest(launching))).hexdigest()
            evidence['invalid_spec'] = {'http_status':422,'audit_id':error.receipt.auditId}
        else:
            raise AssertionError('Expected core Pod validation rejection')

        # An expired/invalid credential on the create operation, after target
        # inspection with the valid fixture identity. No read error is classified.
        bad = copy(cfg)
        bad.cert_file = bad.key_file = None
        bad.api_key = {'BearerToken':'invalid-fixture-token'}
        bad.api_key_prefix = {'BearerToken':'Bearer'}
        bad.refresh_api_key_hook = None
        unauthenticated = client.ApiClient(bad)
        clients.append(unauthenticated)
        invalid_auth = KubernetesProcesses(unauthenticated,row.target,program)
        try:
            await invalid_auth._call(invalid_auth.api.create_namespaced_pod,name,
                invalid_auth.manifest(launching),creation=launching,field_validation='Strict')
        except ClusterCreateRejected as error:
            assert error.receipt.httpStatus == 401 and error.receipt.reason == 'Unauthorized'
            evidence['unauthorized_create'] = {'http_status':401,'audit_id':error.receipt.auditId}
        else:
            raise AssertionError('Expected authentication rejection')
        assert not core.list_namespaced_pod(name).items
        evidence.update(checked_at=datetime.now(timezone.utc).isoformat(),pods_remaining=0)
        (ROOT / 'create-rejection-verification.json').write_text(json.dumps(evidence,indent=2)+'\n')
        print(json.dumps(evidence),flush=True)
    finally:
        if binding is not None:
            rbac.delete_cluster_role_binding(name,body=client.V1DeleteOptions(preconditions=client.V1Preconditions(uid=binding.metadata.uid)))
        if role is not None:
            rbac.delete_cluster_role(name,body=client.V1DeleteOptions(preconditions=client.V1Preconditions(uid=role.metadata.uid)))
        if namespace is not None:
            current = core.read_namespace(name)
            assert current.metadata.uid == namespace.metadata.uid and current.metadata.labels['open-agent.io/fixture'] == 'create-rejection'
            core.delete_namespace(name,body=client.V1DeleteOptions(preconditions=client.V1Preconditions(uid=namespace.metadata.uid)))
        for api in clients:
            api.close()
        admin.close()


if __name__ == '__main__':
    asyncio.run(main())
