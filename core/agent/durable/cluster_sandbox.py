"""Independent, read-only verification of the pinned OpenSandbox Kubernetes profile.

Only a reconciler uses this module. Resource observations bind identities; they
are neither permission to execute tenant code nor proof of physical termination.
Kata/CNI acceptance and the lifecycle service remain separate requirements.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import re
from typing import Annotated
from uuid import UUID

from kubernetes import client
from kubernetes.utils.quantity import parse_quantity
from pydantic import Field

from agent.durable.cluster_control import ClusterSandboxResource, ClusterSandboxTarget, ClusterSnapshot, ClusterSupervisorClient
from agent.durable.cluster_process import ClusterProcessError, KubernetesProcesses, canonical
from agent.durable.supervision import Identifier, WireModel
from agent.platform_runtime.sandbox import RunScope

GROUP, VERSION, PLURAL = 'sandbox.opensandbox.io', 'v1alpha1', 'batchsandboxes'
API_VERSION = GROUP + '/' + VERSION
CR_FINALIZER, POD_FINALIZER = 'open-agent.io/sandbox-workload', 'open-agent.io/sandbox-termination'
POD_OWNER, POD_INDEX = 'batch-sandbox.sandbox.opensandbox.io/name', 'batch-sandbox.sandbox.opensandbox.io/pod-index'
EXECD_DIGEST = 'e11fdd6fa641329a1fe5298e71c8dd2afca742674fe48bc855b2ff6715512829'
# Actual provider output from OpenSandbox 1.1.0 b00c96b0a89c07fc18409fcfe72d170b4c96c8a7.
INSTALLER_SHA256 = 'e96f1db8cffbd0b5c87a0e22fff91e4e0cd827d4bd297c1b59d533d1207c8636'
Name = Annotated[str, Field(max_length=63, pattern=r'^[a-z0-9](?:[-a-z0-9]*[a-z0-9])?$')]


class KataPolicy(WireModel):
    """Operator-owned settings, never accepted from a worker or Skill."""
    controlNamespace: Name
    controlNamespaceUid: Identifier
    runtimeClassUid: Identifier
    runtimeHandler: Name
    imagePullSecret: Name
    execdImage: Annotated[str, Field(max_length=512, pattern=r'^[a-z0-9][a-z0-9.:/_-]*@sha256:' + EXECD_DIGEST + '$')]
    maximumSandboxes: Annotated[int, Field(strict=True, ge=1, le=256)] = 16


def require(condition):
    if not condition:
        raise ClusterProcessError('CLUSTER_SANDBOX_MISMATCH')


def identity(value, name, namespace=None):
    meta = value['metadata']
    require(meta['name'] == name and (namespace is None or meta['namespace'] == namespace))
    require(str(UUID(meta['uid'])) == meta['uid'])
    require(isinstance(meta['resourceVersion'], str) and re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', meta['resourceVersion']))
    require(not meta.get('deletionTimestamp'))
    return {key: meta[key] for key in ('name', 'uid', 'resourceVersion')}


def quantities(value):
    return {key: format(parse_quantity(item).normalize(), 'f') for key, item in value.items()}


class KubernetesSandboxes:
    def __init__(self, processes: KubernetesProcesses, target: ClusterSandboxTarget, policy: KataPolicy):
        require(target.namespace != processes.target.namespace and target.namespaceUid != processes.target.namespaceUid)
        require(policy.controlNamespace != target.namespace and policy.controlNamespaceUid != target.namespaceUid)
        self.processes, self.target, self.policy = processes, target, policy
        api = processes.api_client
        self.core, self.custom = client.CoreV1Api(api), client.CustomObjectsApi(api)
        self.network, self.runtime = client.NetworkingV1Api(api), client.NodeV1Api(api)

    async def _read(self, method, *args, **kwargs):
        # Official SDK transport with a bounded raw response: preserving unknown
        # fields is necessary for exact Pod validation (typed models drop them).
        response = await self.processes._call(method, *args, **kwargs, _preload_content=False)
        def consume():
            try:
                body = response.read(8 * 1024 * 1024 + 1)
                require(len(body) <= 8 * 1024 * 1024)
                result = json.loads(body)
                require(isinstance(result, dict))
                return result
            finally:
                response.close()
                response.release_conn()
        try:
            return await asyncio.to_thread(consume)
        except asyncio.CancelledError:
            raise
        except Exception:
            raise ClusterProcessError('CLUSTER_SANDBOX_UNCONFIRMED') from None

    async def verify_policy(self):
        """Configuration observations only; never a claim that CNI/Kata works."""
        try:
            await self.processes.verify_target()
            env, ns, policy = self.processes.target.environment, self.target.namespace, self.policy
            evidence = {}
            for name, uid, role in [(ns, self.target.namespaceUid, 'sandboxNamespace'),
                    (policy.controlNamespace, policy.controlNamespaceUid, 'controlNamespace')]:
                obj = await self._read(self.core.read_namespace, name)
                evidence[role] = identity(obj, name)
                require(obj['metadata']['uid'] == uid and obj['status']['phase'] == 'Active')
                labels = obj['metadata'].get('labels', {})
                require(labels.get('kubernetes.io/metadata.name') == name and labels.get('open-agent.io/environment') == env)
                if name == ns:
                    require(labels.get('open-agent.io/sandbox-boundary') == 'kata')
            runtime = await self._read(self.runtime.read_runtime_class, self.target.runtimeClass)
            evidence['runtimeClass'] = identity(runtime, self.target.runtimeClass)
            require(runtime['metadata']['uid'] == policy.runtimeClassUid and runtime['handler'] == policy.runtimeHandler)
            # Overhead or scheduling additions need a separately reviewed profile.
            require(set(runtime) <= {'apiVersion', 'kind', 'metadata', 'handler'})
            account = await self._read(self.core.read_namespaced_service_account, 'open-agent-sandbox', ns)
            evidence['serviceAccount'] = identity(account, 'open-agent-sandbox', ns)
            require(account.get('automountServiceAccountToken') is False and not account.get('secrets'))
            require(not account.get('imagePullSecrets'))
            policies = await self._read(self.network.list_namespaced_network_policy, ns, limit=2)
            require(not policies.get('metadata', {}).get('continue') and len(policies['items']) == 1)
            network = policies['items'][0]
            evidence['networkPolicy'] = identity(network, 'open-agent-sandbox-boundary', ns)
            spec = deepcopy(network['spec'])
            spec.setdefault('egress', [])  # Kubernetes omits empty slices.
            require(spec == {'podSelector': {}, 'policyTypes': ['Ingress', 'Egress'], 'egress': [],
                'ingress': [{'from': [{'namespaceSelector': {'matchLabels': {'kubernetes.io/metadata.name': policy.controlNamespace}},
                    'podSelector': {'matchLabels': {'app.kubernetes.io/name': 'open-agent-sandbox-server', 'open-agent.io/environment': env}}}],
                    'ports': [{'port': 44772, 'protocol': 'TCP'}]}]})
            quota = await self._read(self.core.read_namespaced_resource_quota, 'open-agent-sandbox-limits', ns)
            evidence['quota'] = identity(quota, 'open-agent-sandbox-limits', ns)
            count = policy.maximumSandboxes
            hard = {'pods': str(count), 'count/batchsandboxes.sandbox.opensandbox.io': str(count),
                    'requests.cpu': str(count), 'limits.cpu': str(count),
                    **{prefix + '.' + kind: str(count * 512 * 1024 * 1024)
                       for prefix in ('requests', 'limits') for kind in ('memory', 'ephemeral-storage')}}
            require(set(quota['spec']) == {'hard'} and quantities(quota['spec']['hard']) == hard)
            require(quantities(quota['status']['hard']) == hard and set(quota['status']['used']) == set(hard))
            require(all(0 <= parse_quantity(value) <= parse_quantity(hard[key]) for key, value in quota['status']['used'].items()))
            return evidence
        except ClusterProcessError:
            raise
        except Exception:
            raise ClusterProcessError('CLUSTER_SANDBOX_MISMATCH') from None

    def _bound(self, row: ClusterSnapshot):
        self.processes._bound(row)
        require(row.sandboxTarget == self.target and row.isolationProfile == 'kata' and row.podUid is not None)
        require(row.phase in {'observed', 'terminated'} and row.sandboxPhase in {'creating', 'created', 'cleanup_pending'})
        require(isinstance(row.runtimeImage, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/-]*@sha256:[a-f0-9]{64}', row.runtimeImage))

    def _labels(self, row, sandbox_id):
        scope = RunScope(row.lease.scope.tenant, row.target.environment, UUID(row.lease.runId),
                         row.lease.attempt, UUID(row.lease.leaseId), row.deadlineAt)
        return {'opensandbox.io/id': sandbox_id, **scope.metadata()}

    def _spec(self, row, sandbox_id):
        """Independently reviewed allowlist, not a template supplied by a caller."""
        return {'runtimeClassName': self.target.runtimeClass, 'restartPolicy': 'Never',
            'terminationGracePeriodSeconds': 30, 'activeDeadlineSeconds': 3600,
            'automountServiceAccountToken': False, 'serviceAccountName': 'open-agent-sandbox', 'enableServiceLinks': False,
            'dnsPolicy': 'None', 'dnsConfig': {'nameservers': ['127.0.0.1']},
            'nodeSelector': {'open-agent.io/sandbox-pool': 'kata', 'kubernetes.io/os': 'linux'},
            'imagePullSecrets': [{'name': self.policy.imagePullSecret}],
            'securityContext': {'runAsUser': 0, 'runAsGroup': 0, 'seccompProfile': {'type': 'RuntimeDefault'}},
            'containers': [{'name': 'sandbox', 'image': row.runtimeImage,
                'command': ['/opt/opensandbox/bootstrap.sh', '/bin/bash', '-c', 'exec sleep infinity'],
                'env': [{'name': 'EXECD_ACCESS_TOKEN', 'value': '[REDACTED]'}, {'name': 'OPENSANDBOX_ID', 'value': sandbox_id},
                        {'name': 'EXECD_INIT', 'value': '1'}, {'name': 'EXECD', 'value': '/opt/opensandbox/execd'}],
                'resources': {kind: {'cpu': '1', 'memory': '536870912', 'ephemeral-storage': '536870912'} for kind in ('limits', 'requests')},
                'volumeMounts': [{'name': name, 'mountPath': path} for name, path in
                    [('opensandbox-bin', '/opt/opensandbox'), ('workspace', '/workspace'), ('skills', '/skills'), ('tmp', '/tmp')]],
                'securityContext': {'privileged': False, 'allowPrivilegeEscalation': False, 'readOnlyRootFilesystem': True,
                    'capabilities': {'drop': ['ALL'], 'add': ['CHOWN', 'DAC_OVERRIDE', 'FOWNER', 'SETGID', 'SETUID']}}}],
            'volumes': [{'name': 'opensandbox-bin', 'emptyDir': {}},
                {'name': 'workspace', 'emptyDir': {'sizeLimit': '268435456'}},
                {'name': 'skills', 'emptyDir': {'sizeLimit': '33554432'}},
                {'name': 'tmp', 'emptyDir': {'medium': 'Memory', 'sizeLimit': '67108864'}}],
            'initContainers': [{'name': 'execd-installer', 'image': self.policy.execdImage, 'command': ['/bin/sh', '-c'],
                'args': ['[PINNED-INSTALLER]'],
                'resources': {'limits': {'cpu': '0.25', 'memory': '134217728', 'ephemeral-storage': '134217728'},
                              'requests': {'cpu': '0.025', 'memory': '33554432', 'ephemeral-storage': '33554432'}},
                'volumeMounts': [{'name': 'opensandbox-bin', 'mountPath': '/opt/opensandbox'}]}]}

    def _validate_spec(self, value, row, sandbox_id, *, template=False):
        spec = deepcopy(value)
        node = spec.pop('nodeName', None)
        require(not template or node is None)
        require(node is None or isinstance(node, str) and re.fullmatch(r'[a-z0-9][a-z0-9.-]{0,252}', node))
        require(spec.pop('schedulerName', 'default-scheduler') == 'default-scheduler')
        priority = spec.pop('priority', 0)
        require(type(priority) is int and priority == 0)
        require(spec.pop('preemptionPolicy', 'PreemptLowerPriority') == 'PreemptLowerPriority')
        require(spec.pop('serviceAccount', 'open-agent-sandbox') == 'open-agent-sandbox')
        for key in ('hostNetwork', 'hostPID', 'hostIPC', 'shareProcessNamespace'):
            require(spec.pop(key, False) is False)
        tolerations = spec.pop('tolerations', [])
        require(len(tolerations) <= 2)
        for item in tolerations:
            require(item.get('key') in {'node.kubernetes.io/not-ready', 'node.kubernetes.io/unreachable'}
                    and item == {'key': item['key'], 'operator': 'Exists', 'effect': 'NoExecute', 'tolerationSeconds': 300})
        require(len(spec['containers']) == 1 and len(spec['initContainers']) == 1)
        env = spec['containers'][0]['env']
        require(len(env) == 4 and env[0].get('name') == 'EXECD_ACCESS_TOKEN' and set(env[0]) == {'name', 'value'})
        token = env[0]['value']
        require(isinstance(token, str) and re.fullmatch(r'[A-Za-z0-9_-]{64}', token))
        env[0]['value'] = '[REDACTED]'
        installer = spec['initContainers'][0]
        require(len(installer['args']) == 1 and sha256(installer['args'][0].encode()).hexdigest() == INSTALLER_SHA256)
        installer['args'] = ['[PINNED-INSTALLER]']
        for container in (*spec['containers'], *spec['initContainers']):
            require(container.pop('imagePullPolicy', 'IfNotPresent') == 'IfNotPresent')
            require(container.pop('terminationMessagePath', '/dev/termination-log') == '/dev/termination-log')
            require(container.pop('terminationMessagePolicy', 'File') == 'File')
            resources = container['resources']
            require(set(resources) == {'limits', 'requests'})
            for kind in resources:
                resources[kind] = quantities(resources[kind])
        for volume in spec['volumes']:
            if 'sizeLimit' in volume.get('emptyDir', {}):
                volume['emptyDir']['sizeLimit'] = quantities({'size': volume['emptyDir']['sizeLimit']})['size']
        # Canonical JSON comparison keeps booleans distinct from integers.
        require(canonical(spec) == canonical(self._spec(row, sandbox_id)))
        return token, spec

    async def observe(self, row: ClusterSnapshot, sandbox_id: str) -> ClusterSandboxResource:
        try:
            self._bound(row)
            require(str(UUID(sandbox_id)) == sandbox_id and row.sandboxId in {None, sandbox_id})
            policy = await self.verify_policy()
            ns = self.target.namespace
            workload = await self._read(self.custom.get_namespaced_custom_object, GROUP, VERSION, ns, PLURAL, sandbox_id)
            cr = identity(workload, sandbox_id, ns)
            require(workload.get('apiVersion') == API_VERSION and workload.get('kind') == 'BatchSandbox')
            meta, spec = workload['metadata'], deepcopy(workload['spec'])
            labels = self._labels(row, sandbox_id)
            require(meta.get('labels') == labels and meta.get('finalizers') == [CR_FINALIZER] and not meta.get('ownerReferences'))
            # Pinned official CRD defaults this lifecycle policy even in normal mode.
            require(spec.pop('taskResourcePolicyWhenCompleted', 'Retain') == 'Retain')
            require(set(spec) == {'replicas', 'expireTime', 'template'} and type(spec['replicas']) is int and spec['replicas'] == 1)
            expires = datetime.fromisoformat(spec['expireTime'].replace('Z', '+00:00'))
            require(expires.tzinfo is not None and expires <= row.deadlineAt + timedelta(seconds=1))
            template = spec['template']
            require(set(template) == {'metadata', 'spec'})
            template_meta = deepcopy(template['metadata'])
            if template_meta.get('annotations') == {}:
                template_meta.pop('annotations')
            require(template_meta == {'labels': labels, 'finalizers': [POD_FINALIZER]})
            template_token, shape = self._validate_spec(template['spec'], row, sandbox_id, template=True)
            pod = await self._read(self.core.read_namespaced_pod, sandbox_id + '-0', ns)
            observed = identity(pod, sandbox_id + '-0', ns)
            meta = pod['metadata']
            require(meta.get('finalizers') == [POD_FINALIZER])
            require(meta.get('labels') == {**labels, POD_OWNER: sandbox_id, POD_INDEX: '0'})
            require(not meta.get('annotations'))
            require(meta.get('ownerReferences') == [{'apiVersion': API_VERSION, 'kind': 'BatchSandbox',
                    'name': sandbox_id, 'uid': cr['uid'], 'controller': True, 'blockOwnerDeletion': True}])
            token, _ = self._validate_spec(pod['spec'], row, sandbox_id)
            require(token == template_token and len({cr['uid'], observed['uid'], row.podUid}) == 3)
            # Scan a bounded full namespace so an unlabeled extra owner Pod is
            # detected too. A partial list is never evidence of uniqueness.
            pods = await self._read(self.core.list_namespaced_pod, ns, limit=self.policy.maximumSandboxes + 1)
            require(not pods.get('metadata', {}).get('continue') and len(pods['items']) <= self.policy.maximumSandboxes)
            matching = [item for item in pods['items'] if
                item['metadata'].get('labels', {}).get('oa.lease') == row.lease.leaseId
                or item['metadata'].get('labels', {}).get('opensandbox.io/id') == sandbox_id
                or any(owner.get('uid') == cr['uid'] for owner in item['metadata'].get('ownerReferences', []))]
            require(len(matching) == 1 and matching[0]['metadata']['uid'] == observed['uid']
                    and matching[0]['metadata']['resourceVersion'] == observed['resourceVersion'])
            workloads = await self._read(self.custom.list_namespaced_custom_object, GROUP, VERSION, ns, PLURAL,
                                          label_selector='oa.lease=' + row.lease.leaseId, limit=2)
            require(not workloads.get('metadata', {}).get('continue') and len(workloads['items']) == 1
                    and workloads['items'][0]['metadata']['uid'] == cr['uid']
                    and workloads['items'][0]['metadata']['resourceVersion'] == cr['resourceVersion'])
            if row.sandboxResource is not None:
                require(row.sandboxResource.sandboxId == sandbox_id and row.sandboxResource.workloadUid == cr['uid']
                        and row.sandboxResource.podUid == observed['uid'])
                return row.sandboxResource  # Preserve the original immutable receipt/RVs.
            evidence = {'schema': 1, 'lease': row.lease.model_dump(), 'target': row.target.model_dump(),
                'sandboxTarget': self.target.model_dump(), 'policy': policy, 'workload': cr, 'pod': observed,
                'labels': labels, 'normalizedSpecSha256': sha256(canonical(shape)).hexdigest()}
            return ClusterSandboxResource(sandboxId=sandbox_id, workloadUid=cr['uid'], workloadResourceVersion=cr['resourceVersion'],
                podUid=observed['uid'], podResourceVersion=observed['resourceVersion'], evidenceSha256=sha256(canonical(evidence)).hexdigest())
        except ClusterProcessError:
            raise
        except Exception:
            raise ClusterProcessError('CLUSTER_SANDBOX_MISMATCH') from None


class ClusterSandboxBinding:
    """Trusted coordinator; no create/delete/run methods and no execution permit."""
    def __init__(self, control: ClusterSupervisorClient, sandboxes: KubernetesSandboxes):
        require(control.target == sandboxes.processes.target and control.sandbox_target == sandboxes.target)
        self.control, self.sandboxes = control, sandboxes

    async def prepare(self, lease_id: str, image: str):
        row = await self.control.inspect(lease_id)
        now = datetime.now(timezone.utc)
        require(row.phase == 'observed' and row.state == 'running' and row.leaseState == 'held'
                and row.heartbeatUntil > now and row.deadlineAt > now)
        pod = await self.sandboxes.processes.inspect(row)
        require(pod is not None and pod.metadata.deletion_timestamp is None and pod.spec.node_name
                and not pod.spec.scheduling_gates and pod.status.phase == 'Running')
        await self.sandboxes.verify_policy()
        # The final transaction rechecks the live lease after external I/O.
        return await self.control.prepare_sandbox(lease_id, image)

    async def bind(self, lease_id: str, sandbox_id: str):
        row = await self.control.inspect(lease_id)
        resource = await self.sandboxes.observe(row, sandbox_id)
        # A late observation may be persisted after cancellation, but grants no
        # permission and cannot release quota. Never recreate on response loss.
        return await self.control.sandbox_resource(lease_id, resource)
