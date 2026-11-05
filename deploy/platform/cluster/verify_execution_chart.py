"""Apply actual Helm output to the owned, agentless K3s fixture and exercise RBAC/CEL.

No kubelet, model, tenant code or business database is used. Credentials remain
in the fixture's private files/memory; only bounded non-secret evidence is saved.
"""
import asyncio
from copy import copy, deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import sys
import time
from uuid import uuid4

ROOT = Path('/opt/open-agent/k3s-api-fixture')
INPUTS = Path(__file__).resolve().parent
manifest = json.loads((INPUTS / 'cluster-contract-manifest.json').read_bytes())
overlay = Path('/opt/open-agent/cluster-contracts') / manifest['sha256']
sys.path[:0] = [str(overlay / 'source'), str(overlay / 'vendor')]

from kubernetes import client, config, utils
from kubernetes.client.exceptions import ApiException
from urllib3.util.retry import Retry
import yaml

from agent.durable.cluster_control import ClusterSnapshot
from agent.durable.cluster_process import ClusterProgram, KubernetesProcesses, ClusterProcessError
from agent.tests.test_cluster_control import snapshot


def require_status(status, action):
    try:
        action()
    except ApiException as error:
        assert error.status in (status if isinstance(status, tuple) else (status,)), error.status
        return error
    raise AssertionError('Expected Kubernetes API rejection')


def service_client(admin, namespace, name):
    core = client.CoreV1Api(admin)
    token = core.create_namespaced_service_account_token(name, namespace,
        {'spec': {'audiences': [], 'expirationSeconds': 600}}, _request_timeout=(5, 15)).status.token
    settings = copy(admin.configuration)
    settings.cert_file = settings.key_file = None
    settings.username = settings.password = None
    settings.api_key = {'BearerToken': token}
    settings.api_key_prefix = {'BearerToken': 'Bearer'}
    settings.refresh_api_key_hook = None
    api = client.ApiClient(settings)
    subject = client.AuthenticationV1Api(api).create_self_subject_review(
        {'apiVersion': 'authentication.k8s.io/v1', 'kind': 'SelfSubjectReview'}, _request_timeout=(5, 15))
    assert subject.status.user_info.username == f'system:serviceaccount:{namespace}:{name}'
    return api


async def main():
    os.umask(0o077)
    assert (ROOT / 'owner').read_text() == 'open-agent-k3s-api-fixture-v1'
    cfg = client.Configuration()
    config.load_kube_config(config_file=str(ROOT / 'admin.yaml'), client_configuration=cfg, persist_config=False)
    cfg.retries = Retry(total=0, connect=0, read=0, redirect=0, status=0)
    cfg.debug = False
    admin = client.ApiClient(cfg)
    core, admission = client.CoreV1Api(admin), client.AdmissionregistrationV1Api(admin)
    assert client.VersionApi(admin).get_code().git_version == 'v1.36.6+k3s1'
    assert not core.list_node().items
    created, bindings, clients, namespaces = [], [], [], []
    evidence = {'version': 'v1.36.6+k3s1', 'overlay_sha256': manifest['sha256'],
                'kubelet_or_cni_or_kata_verified': False, 'environments': {}, 'rendered_sha256': {}}
    stage = 'namespaces'
    try:
        for environment in ['test', 'prod']:
            for name in ['open-agent-' + environment, 'open-agent-executors-' + environment]:
                value = core.create_namespace({'metadata': {'name': name, 'labels': {
                    'open-agent.io/fixture': 'execution-chart', 'pod-security.kubernetes.io/enforce': 'restricted',
                    'pod-security.kubernetes.io/enforce-version': 'v1.36'}}}, _request_timeout=(5, 15))
                namespaces.append((name, value.metadata.uid))
            raw_yaml = (INPUTS / ('execution-' + environment + '.yaml')).read_bytes()
            evidence['rendered_sha256'][environment] = sha256(raw_yaml).hexdigest()
            docs = [doc for doc in yaml.safe_load_all(raw_yaml) if doc]
            for doc in docs:
                utils.create_from_dict(admin, doc, namespace=doc['metadata'].get('namespace', 'default'))
                created.append(doc)
                if doc['kind'] == 'ValidatingAdmissionPolicyBinding': bindings.append(doc['metadata']['name'])
            policy_name = next(d['metadata']['name'] for d in docs if d['kind'] == 'ValidatingAdmissionPolicy')
            for _ in range(50):
                policy = admission.read_validating_admission_policy(policy_name)
                if policy.status and policy.status.observed_generation == policy.metadata.generation:
                    break
                await asyncio.sleep(.2)
            else:
                raise RuntimeError('Admission type-checking did not settle')
            warnings = policy.status.type_checking.expression_warnings if policy.status.type_checking else []
            if warnings:
                (ROOT / 'execution-policy-warnings.json').write_text(json.dumps(admin.sanitize_for_serialization(warnings), indent=2))
                raise RuntimeError('Admission CEL type warnings')
            supervisor_name = next(d['metadata']['name'] for d in docs if d['kind'] == 'ServiceAccount' and d['metadata']['namespace'] == 'open-agent-' + environment)
            supervisor = service_client(admin, 'open-agent-' + environment, supervisor_name)
            executor = service_client(admin, 'open-agent-executors-' + environment, 'open-agent-executor')
            clients += [supervisor, executor]
            peer = client.CoreV1Api(supervisor)
            ns = 'open-agent-executors-' + environment
            allowed_ns = peer.read_namespace(ns)
            cluster = peer.read_namespace('kube-system')
            denied = []
            actions = {
                'read_worker_secret': lambda: peer.read_namespaced_secret('open-agent-' + environment + '-worker', ns),
                'list_pods': lambda: peer.list_namespaced_pod(ns),
                'list_nodes': lambda: peer.list_node(),
                'read_foreign_namespace': lambda: peer.read_namespace('default'),
                'delete_node': lambda: peer.delete_node('fixture-node-absent'),
                'cross_environment_pods': lambda: peer.list_namespaced_pod('open-agent-executors-' + ('prod' if environment == 'test' else 'test')),
                'executor_read_namespace': lambda: client.CoreV1Api(executor).read_namespace(ns),
                'executor_read_secret': lambda: client.CoreV1Api(executor).read_namespaced_secret('open-agent-' + environment + '-worker', ns),
            }
            for name, action in actions.items():
                stage = environment + '/rbac/' + name
                require_status(403, action)
                denied.append(name)
            require_status(404, lambda: peer.read_node('fixture-node-absent'))  # Authorized GET, no synthetic node.
            worker_image = next(d for d in docs if d['kind'] == 'Deployment')['spec']['template']['spec']['containers'][0]['image']
            raw = snapshot()
            raw['target'].update(environment=environment, clusterId=cluster.metadata.uid, namespace=ns,
                                 namespaceUid=allowed_ns.metadata.uid, executorImage=worker_image)
            raw['lease']['scope']['environment'] = environment
            row = ClusterSnapshot.model_validate(raw).model_copy(update={'phase': 'launching'})
            program = ClusterProgram('open-agent-' + environment + '-worker', 'open-agent-executor', 'trusted',
                'open-agent-' + environment + '-ca', ('open-agent-' + environment + '-registry',))
            driver = KubernetesProcesses(supervisor, row.target, program)
            original = driver.manifest(row)
            # Allow policy/binding informer propagation, requiring an actual rejection.
            bad = deepcopy(original)
            bad['metadata']['name'] = 'oa-exec-' + uuid4().hex
            invalid_image = 'registry.invalid/changed@sha256:' + 'b' * 64
            bad['spec']['containers'][0]['image'] = invalid_image
            for _ in range(50):
                try:
                    peer.create_namespaced_pod(ns, bad, dry_run='All')
                except ApiException as error:
                    if error.status in (403, 422) and policy_name in error.body:
                        break
                    raise
                await asyncio.sleep(.2)
            else:
                raise RuntimeError('Admission binding not enforced')
            mutations = {
                'image': lambda p: p['spec']['containers'][0].update(image=invalid_image),
                'command': lambda p: p['spec']['containers'][0].update(command=['/bin/sh', '-c', 'true']),
                'worker_secret': lambda p: p['spec']['volumes'][0]['secret'].update(secretName='foreign-secret'),
                'registry_secret': lambda p: p['spec'].update(imagePullSecrets=[{'name': 'foreign-registry'}]),
                'no_gate': lambda p: p['spec'].pop('schedulingGates'),
                'writable_root': lambda p: p['spec']['containers'][0]['securityContext'].update(readOnlyRootFilesystem=False),
                'unbounded_resources': lambda p: p['spec']['containers'][0]['resources']['limits'].update(memory='2Gi'),
                'oversized_temporary_volume': lambda p: p['spec']['volumes'][1]['emptyDir'].update(sizeLimit='128Mi'),
                'unbounded_temporary_volume': lambda p: p['spec']['volumes'][1]['emptyDir'].pop('sizeLimit'),
                'extra_environment': lambda p: p['spec']['containers'][0]['env'].append({'name': 'UNREVIEWED', 'value': 'synthetic'}),
                'service_account_token': lambda p: p['spec'].update(automountServiceAccountToken=True),
                'privileged': lambda p: p['spec']['containers'][0]['securityContext'].update(privileged=True),
                'host_network': lambda p: p['spec'].update(hostNetwork=True),
                'host_path': lambda p: p['spec']['volumes'].__setitem__(1, {'name': 'tmp', 'hostPath': {'path': '/'}}),
                'extra_container': lambda p: p['spec']['containers'].append(deepcopy(p['spec']['containers'][0]) | {'name': 'extra'}),
                'foreign_environment': lambda p: p['metadata']['labels'].update({'open-agent.io/environment': 'other'}),
            }
            policy_rejections = {}
            for name, mutate in mutations.items():
                stage = environment + '/admission/' + name
                bad = deepcopy(original)
                bad['metadata']['name'] = 'oa-exec-' + uuid4().hex
                mutate(bad)
                error = require_status((403, 422), lambda: peer.create_namespaced_pod(ns, bad, dry_run='All'))
                policy_rejections[name] = {'status': error.status, 'policy_named': policy_name in error.body}
                if name in {'image', 'command', 'worker_secret', 'registry_secret', 'no_gate', 'writable_root', 'extra_environment'}:
                    assert policy_rejections[name]['policy_named'], name
            # The accepted Pod is created by the actual SDK driver and starts gated.
            stage = environment + '/accepted-pod-lifecycle'
            pod = await driver.launch(row)
            row = row.model_copy(update={'phase': 'observed', 'podUid': pod.metadata.uid, 'observedResourceVersion': pod.metadata.resource_version})
            await driver.activate(row)
            row = row.model_copy(update={'state': 'cancelled', 'leaseState': 'cleanup_pending'})
            for _ in range(15):
                pod = await driver.inspect(row)
                try:
                    await driver.request_stop(row, pod)
                    break
                except ClusterProcessError:
                    await asyncio.sleep(.2)
            else:
                raise RuntimeError('Pod stop not confirmed')
            pod = await driver.inspect(row)
            receipt = driver.not_started_receipt(row, pod)
            row = row.model_copy(update={'phase': 'not_started', 'notStarted': receipt})
            await driver.finalize(row)
            for _ in range(30):
                if await driver.absent_after_receipt(row): break
                await asyncio.sleep(.2)
            else:
                raise RuntimeError('Pod removal not confirmed')
            evidence['environments'][environment] = {'rbac_denied': denied, 'policy_rejections': policy_rejections,
                'cel_type_warnings': 0, 'actual_sdk_create_activate_delete_finalize': True,
                'pod_uid': row.podUid, 'namespace_uid': row.target.namespaceUid,
                'pods_remaining': len(core.list_namespaced_pod(ns).items)}
        evidence['checked_at'] = datetime.now(timezone.utc).isoformat()
        (ROOT / 'execution-chart-verification.json').write_text(json.dumps(evidence, indent=2) + '\n')
        print(json.dumps(evidence), flush=True)
    except Exception as error:
        diagnostic = {'stage': stage, 'error_type': type(error).__name__, 'message': str(error)[:1000] if not isinstance(error, ApiException) else 'Kubernetes API request failed',
                      'status': getattr(error, 'status', None), 'body': getattr(error, 'body', None)}
        (ROOT / 'execution-chart-diagnostic.json').write_text(json.dumps(diagnostic, indent=2) + '\n')
        raise RuntimeError('Execution chart API verification failed; inspect private diagnostic') from None
    finally:
        for name in reversed(bindings):
            try: admission.delete_validating_admission_policy_binding(name)
            except ApiException as error:
                if error.status != 404: raise
        for name, uid in reversed(namespaces):
            current = core.read_namespace(name)
            assert current.metadata.uid == uid and current.metadata.labels['open-agent.io/fixture'] == 'execution-chart'
            for pod in core.list_namespaced_pod(name).items:
                if pod.metadata.finalizers:
                    core.patch_namespaced_pod(pod.metadata.name, name, {'metadata': {'finalizers': []}})
            core.delete_namespace(name, body=client.V1DeleteOptions(preconditions=client.V1Preconditions(uid=uid)))
        for doc in reversed(created):
            if doc['kind'] == 'ValidatingAdmissionPolicy': admission.delete_validating_admission_policy(doc['metadata']['name'])
            if doc['kind'] == 'ClusterRoleBinding': client.RbacAuthorizationV1Api(admin).delete_cluster_role_binding(doc['metadata']['name'])
            if doc['kind'] == 'ClusterRole': client.RbacAuthorizationV1Api(admin).delete_cluster_role(doc['metadata']['name'])
        for api in clients: api.close()
        admin.close()


if __name__ == '__main__':
    asyncio.run(main())
