"""Run the pinned official controller against the owned, agentless API fixture.

No production credentials, sandbox nodes or tenant code. Only fixture credentials
are mounted read-only. Stop this container before removing the network and API.
"""
import asyncio
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess

from kubernetes import client
import yaml

ROOT = Path('/opt/open-agent/k3s-api-fixture')
NAME = 'open-agent-controller-api-contract'
OWNER = 'open-agent-kata-controller-contract-v1'
# The official umbrella image was built from the preceding source commit.
# All 319 kubernetes/ files match the umbrella commit byte-for-byte; see
# deploy/platform/kata/controller-source.json for both source archive digests.
COMMIT = '27bc1b7c87715fb35ec391b7a55658653dbbf611'
CONFIG_ID = 'sha256:ab51af7657c93f5a4a7445c8b41fa5e4a25ce86187c3e10bff8db8b9cc9caa35'


class ControllerFixture:
    def __init__(self):
        self.container_id = None
        self.bridge, self.zone_added = None, False
        self.credentials = ROOT / 'controller-private'
        self.image = json.loads(Path('/opt/open-agent/kata-controller/image.json').read_bytes())
        assert self.image['config_id'] == CONFIG_ID
        self.evidence = {}

    def command(self, *args, required=True):
        result = subprocess.run(['docker', *args], capture_output=True, timeout=60)
        if required and result.returncode: raise RuntimeError('controller_fixture_docker_failed')
        return result

    def owned_container(self):
        obj = json.loads(self.command('inspect', self.container_id).stdout)[0]
        assert obj['Id'] == self.container_id and obj['Config']['Labels']['open-agent.owner'] == OWNER
        assert obj['Name'] == '/' + NAME and set(obj['NetworkSettings']['Networks']) == {'open-agent-k3s-api-contract'}
        return obj

    def logs(self):
        self.owned_container()
        result = self.command('logs', self.container_id)
        # Controller errors can include workload envs. Keep the full fixture log private.
        raw = result.stdout + result.stderr
        (ROOT / 'controller-private.log').write_bytes(raw)
        return raw

    async def start(self, api, namespace, inputs):
        os.umask(0o077)
        assert (ROOT / 'owner').read_text() == 'open-agent-k3s-api-fixture-v1'
        assert self.command('container', 'inspect', NAME, required=False).returncode != 0
        core, crds, rbac = client.CoreV1Api(api), client.ApiextensionsV1Api(api), client.RbacAuthorizationV1Api(api)
        assert not core.list_node().items
        for name in ['sandbox.opensandbox.io_pools.yaml', 'sandbox.opensandbox.io_sandboxsnapshots.yaml']:
            raw = yaml.safe_load((inputs / name).read_bytes())
            crds.create_custom_resource_definition(raw, field_validation='Strict')
            for _ in range(60):
                obj = crds.read_custom_resource_definition(raw['metadata']['name'])
                if any(c.type == 'Established' and c.status == 'True' for c in obj.status.conditions or []): break
                await asyncio.sleep(.5)
            else: raise RuntimeError('controller_fixture_crd_not_established')
        core.create_namespaced_service_account(namespace, {'metadata': {'name': NAME}, 'automountServiceAccountToken': False})
        role = yaml.safe_load((inputs / 'controller-upstream-role.yaml').read_bytes())
        role['metadata'] = {'name': NAME}
        rbac.create_cluster_role(role)
        rbac.create_cluster_role_binding({'metadata': {'name': NAME}, 'roleRef': {'apiGroup': 'rbac.authorization.k8s.io', 'kind': 'ClusterRole', 'name': NAME},
            'subjects': [{'kind': 'ServiceAccount', 'name': NAME, 'namespace': namespace}]})
        rbac.create_namespaced_role(namespace, {'metadata': {'name': NAME}, 'rules': [{'apiGroups': ['coordination.k8s.io'],
            'resources': ['leases'], 'verbs': ['get', 'list', 'watch', 'create', 'update', 'patch']}]})
        rbac.create_namespaced_role_binding(namespace, {'metadata': {'name': NAME},
            'roleRef': {'apiGroup': 'rbac.authorization.k8s.io', 'kind': 'Role', 'name': NAME},
            'subjects': [{'kind': 'ServiceAccount', 'name': NAME, 'namespace': namespace}]})
        token = core.create_namespaced_service_account_token(NAME, namespace,
            client.AuthenticationV1TokenRequest(spec=client.V1TokenRequestSpec(audiences=[], expiration_seconds=1800))).status.token
        configuration = yaml.safe_load((ROOT / 'admin.yaml').read_bytes())
        # Reuse only the public server/CA details. Replace the admin identity entirely.
        configuration['users'] = [{'name': NAME, 'user': {'token': token}}]
        configuration['contexts'] = [{'name': NAME, 'context': {'cluster': configuration['clusters'][0]['name'], 'user': NAME, 'namespace': namespace}}]
        configuration['current-context'] = NAME
        assert not self.credentials.is_symlink()
        self.credentials.mkdir(mode=0o700, exist_ok=False)
        for name, data in [('config', yaml.safe_dump(configuration)), ('namespace', namespace)]:
            path = self.credentials / name
            path.write_text(data)
            path.chmod(0o444)
        self.credentials.chmod(0o555)
        inspected = json.loads(self.command('image', 'inspect', self.image['image']).stdout)[0]
        assert inspected['Id'] == self.image['config_id']
        network = json.loads(self.command('network', 'inspect', 'open-agent-k3s-api-contract').stdout)[0]
        assert network['Internal'] and network['Labels']['open-agent.owner'] == 'open-agent-k3s-api-fixture-v1'
        self.bridge = 'br-' + network['Id'][:12]
        # Docker normally associates its bridge with the docker firewalld zone.
        # Only repair the disposable bridge, runtime only; never reload/flush.
        if subprocess.run(['systemctl', 'is-active', '--quiet', 'firewalld']).returncode == 0:
            zone = subprocess.run(['firewall-cmd', '--get-zone-of-interface=' + self.bridge], capture_output=True, text=True).stdout.strip()
            assert zone in {'', 'no zone', 'docker'}
            if zone != 'docker':
                subprocess.run(['firewall-cmd', '--zone=docker', '--add-interface=' + self.bridge], check=True, capture_output=True)
                self.zone_added = True
        args = ['run', '-d', '--name', NAME, '--label', 'open-agent.owner=' + OWNER,
            '--network=open-agent-k3s-api-contract', '--read-only', '--user=65532:65532', '--cap-drop=ALL',
            '--security-opt=no-new-privileges', '--memory=384m', '--memory-swap=384m', '--cpus=0.5', '--pids-limit=128',
            '--tmpfs=/tmp:rw,nosuid,nodev,size=16m', '--env=KUBECONFIG=/fixture/config', '--env=POD_NAMESPACE=' + namespace,
            '--mount', 'type=bind,source=' + str(self.credentials) + ',target=/fixture,readonly',
            '--mount', 'type=bind,source=' + str(self.credentials / 'namespace') + ',target=/var/run/secrets/kubernetes.io/serviceaccount/namespace,readonly',
            self.image['image'], '--leader-elect', '--metrics-bind-address=0', '--health-probe-bind-address=127.0.0.1:8081',
            '--concurrency=batchsandbox=1;pool=1', '--kube-client-qps=10', '--kube-client-burst=20']
        self.container_id = self.command(*args).stdout.decode().strip()
        (ROOT / 'controller-installation.json').write_text(json.dumps({'owner': OWNER, 'container_id': self.container_id, 'image': self.image['config_id']}))
        coordination = client.CoordinationV1Api(api)
        for _ in range(90):
            assert self.owned_container()['State']['Running']
            entries = coordination.list_namespaced_lease(namespace).items
            if any(item.spec.holder_identity for item in entries): break
            await asyncio.sleep(.5)
        else: raise RuntimeError('controller_fixture_leadership_timeout')
        logs = self.logs()
        startup = [json.loads(line) for line in logs.splitlines() if b'"Starting controller"' in line]
        assert len(startup) == 1 and startup[0]['commitID'] == COMMIT[:7]
        self.evidence = {'image': self.image['config_id'], 'source_commit': startup[0]['commitID'],
            'leader_election_observed': True, 'uid': 65532, 'read_only': True, 'capabilities_dropped': True,
            'public_ports': False, 'api_credentials': 'short-lived dedicated fixture ServiceAccount, no admin identity'}

    async def restart(self):
        self.owned_container()
        self.command('restart', '--time', '15', self.container_id)
        assert self.owned_container()['State']['Running']

    async def settlement(self, api, driver, row):
        """Observe real controller convergence; never a production quota-release proof."""
        from agent.durable.cluster_control import PodTermination
        from agent.durable.cluster_process import ClusterProcessError
        from uuid import uuid4
        namespace, name = row.sandboxTarget.namespace, row.sandboxId
        workload_uid, pod_uid = row.sandboxResource.workloadUid, row.sandboxResource.podUid
        now = datetime.now(timezone.utc)
        # Synthetic business ledger, explicitly NOT real kubelet termination.
        terminal = row.model_copy(update={'phase': 'terminated', 'state': 'cancelled',
            'leaseState': 'cleanup_pending', 'sandboxPhase': 'cleanup_pending',
            'termination': PodTermination(id=str(uuid4()), podUid=row.podUid, resourceVersion='1',
                nodeName='synthetic-executor-node', nodeUid=str(uuid4()), podPhase='Failed',
                containerName='executor', containerId='containerd://'+'a'*64, exitCode=137,
                startedAt=now-timedelta(seconds=1), finishedAt=now, evidenceSha256='b'*64)})
        custom, core = client.CustomObjectsApi(api), client.CoreV1Api(api)
        args = ('sandbox.opensandbox.io', 'v1alpha1', namespace, 'batchsandboxes', name)
        def read():
            obj = custom.get_namespaced_custom_object(*args)
            assert obj['metadata']['uid'] == workload_uid
            return obj
        async def acknowledged(generation):
            for _ in range(120):
                obj = read()
                assert obj['spec']['replicas'] == 0 and obj['metadata']['generation'] == generation
                assert not obj['metadata'].get('deletionTimestamp')
                if obj.get('status', {}).get('observedGeneration') == generation and obj.get('status', {}).get('pauseObservedGeneration') == generation:
                    receipt = await driver.quiesce(terminal)
                    assert receipt is not None and receipt.generation == generation and receipt.workloadUid == workload_uid
                    return obj, receipt
                await asyncio.sleep(.5)
            raise RuntimeError('controller_generation_not_acknowledged')
        original = read()
        await driver.quiesce(terminal)
        changed = read()
        zero, first_receipt = await acknowledged(changed['metadata']['generation'])
        pod = core.read_namespaced_pod(name + '-0', namespace)
        assert pod.metadata.uid == pod_uid and not pod.spec.node_name and not pod.status.container_statuses
        assert not pod.metadata.deletion_timestamp and not core.list_node().items
        # All nodes are absent in this owned test cluster; no process ever ran.
        # Replica zero alone has deliberately not removed the existing Pod.
        core.delete_namespaced_pod(pod.metadata.name, namespace, body=client.V1DeleteOptions(grace_period_seconds=30,
            preconditions=client.V1Preconditions(uid=pod_uid, resource_version=pod.metadata.resource_version)))
        pod = core.read_namespaced_pod(name + '-0', namespace)
        assert pod.metadata.uid == pod_uid and pod.metadata.deletion_timestamp and not pod.spec.node_name
        assert pod.metadata.finalizers == ['open-agent.io/sandbox-termination']
        core.patch_namespaced_pod(pod.metadata.name, namespace, [
            {'op': 'test', 'path': '/metadata/uid', 'value': pod_uid},
            {'op': 'test', 'path': '/metadata/resourceVersion', 'value': pod.metadata.resource_version},
            {'op': 'test', 'path': '/metadata/finalizers', 'value': pod.metadata.finalizers},
            {'op': 'replace', 'path': '/metadata/finalizers', 'value': []}], _content_type='application/json-patch+json')
        for _ in range(40):
            if not core.list_namespaced_pod(namespace).items: break
            await asyncio.sleep(.5)
        else: raise RuntimeError('fixture_pod_not_removed')
        await self.restart()
        current = read()
        expiry = datetime.fromisoformat(current['spec']['expireTime'].replace('Z', '+00:00')) - timedelta(seconds=1)
        updated = custom.patch_namespaced_custom_object(*args, {'metadata': {'uid': workload_uid,
            'resourceVersion': current['metadata']['resourceVersion']}, 'spec': {'expireTime': expiry.isoformat()}})
        restarted, restart_receipt = await acknowledged(updated['metadata']['generation'])
        assert not core.list_namespaced_pod(namespace).items
        try:
            custom.patch_namespaced_custom_object(*args, {'metadata': {'uid': workload_uid,
                'resourceVersion': original['metadata']['resourceVersion']}, 'spec': {'replicas': 1}})
        except client.ApiException as error: assert error.status == 409
        else: raise RuntimeError('stale_replica_change_was_accepted')
        # Deleting first prevents a later generation from being acknowledged.
        custom.delete_namespaced_custom_object(*args, body={'preconditions': {'uid': workload_uid}})
        deleting = read()
        assert deleting['metadata']['deletionTimestamp'] and deleting['metadata']['finalizers'] == ['open-agent.io/sandbox-workload']
        changed_after_delete = custom.patch_namespaced_custom_object(*args, {'metadata': {'uid': workload_uid,
            'resourceVersion': deleting['metadata']['resourceVersion']}, 'spec': {'expireTime': (expiry-timedelta(seconds=1)).isoformat()}})
        try:
            await driver.quiesce(terminal)
        except ClusterProcessError:
            pass
        else:
            raise RuntimeError('deleting_cr_quiescence_was_accepted')
        for _ in range(20):
            obj = read()
            assert obj['metadata']['generation'] == changed_after_delete['metadata']['generation']
            assert obj['status']['observedGeneration'] < obj['metadata']['generation']
            assert not core.list_namespaced_pod(namespace).items
            await asyncio.sleep(.5)
        self.logs()
        return {'replica_zero_generation': zero['metadata']['generation'],
            'backend_quiescence_adapter_exercised': True,
            'first_receipt': first_receipt.model_dump(), 'restart_receipt': restart_receipt.model_dump(),
            'executor_termination': 'synthetic ledger precondition; no executor or kubelet in fixture',
            'replica_zero_acknowledged': True, 'existing_pod_preserved_until_explicit_delete': True,
            'controller_restart_generation': restarted['metadata']['generation'], 'restart_acknowledged': True,
            'pod_not_recreated_after_restart': True, 'stale_resource_version_rejected': True,
            'deleting_cr_generation': changed_after_delete['metadata']['generation'],
            'deleting_cr_quiescence_rejected': True,
            'deleting_cr_unacknowledged_during_observation_seconds': 10,
            'physical_kubelet_termination_verified': False, 'quota_released': False}

    def stop(self):
        if self.container_id:
            self.logs()
            self.command('stop', '--time', '15', self.container_id)
            self.command('rm', self.container_id)
            self.container_id = None
        if self.credentials.exists():
            assert not self.credentials.is_symlink() and self.credentials.resolve().parent == ROOT
            self.credentials.chmod(0o700)
            for name in ('config', 'namespace'):
                path = self.credentials / name
                assert path.is_file() and not path.is_symlink()
                path.unlink()
            self.credentials.rmdir()
        if self.zone_added:
            current = subprocess.run(['firewall-cmd', '--get-zone-of-interface=' + self.bridge], capture_output=True, text=True).stdout.strip()
            assert current == 'docker'
            subprocess.run(['firewall-cmd', '--zone=docker', '--remove-interface=' + self.bridge], check=True, capture_output=True)
            self.zone_added = False
        result = {'controller_removed': True, 'controller_credentials_removed': True,
                  'temporary_bridge_zone_restored': True,
                  'checked_at': datetime.now(timezone.utc).isoformat()}
        (ROOT / 'controller-cleanup.json').write_text(json.dumps(result, indent=2)+'\n')
