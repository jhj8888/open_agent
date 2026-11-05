"""Kubernetes executor processes, using the official SDK and immutable lease identities.

Only the trusted reconciler imports this module. A missing Pod is NOT proof that
its process stopped. The caller must persist a termination receipt before finalize.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from hashlib import sha256
import json
import re
from uuid import UUID, uuid5

from kubernetes import client, config
from kubernetes.client.exceptions import ApiException
from urllib3.util.retry import Retry

from agent.durable.cluster_control import ClusterSnapshot, ClusterTarget, PodTermination, PodNotStarted

FINALIZER = "open-agent.io/executor-termination"
SCHEDULING_GATE = "open-agent.io/execution-authorized"
PREFIX = "open-agent.io/"


class ClusterProcessError(RuntimeError):
    """Fixed error codes only; SDK exceptions can contain credentials or bodies."""


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def clean(value):
    if isinstance(value, dict):
        return {key: clean(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [clean(item) for item in value]
    return value


@dataclass(frozen=True)
class ClusterProgram:
    """Operator-owned names only, never tenant content or raw credentials."""
    worker_secret: str
    service_account: str
    node_pool: str
    ca_config_map: str | None = None
    image_pull_secrets: tuple[str, ...] = ()

    def __post_init__(self):
        values = (self.worker_secret, self.service_account, self.node_pool)
        if (not isinstance(self.image_pull_secrets, tuple) or len(self.image_pull_secrets) > 4
                or any(not isinstance(value, str) for value in self.image_pull_secrets)
                or len(set(self.image_pull_secrets)) != len(self.image_pull_secrets)):
            raise ClusterProcessError("CLUSTER_PROGRAM_INVALID")
        values += self.image_pull_secrets
        if self.ca_config_map is not None:
            values += (self.ca_config_map,)
        for value in values:
            if not isinstance(value, str) or not re.fullmatch(r"[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?", value):
                raise ClusterProcessError("CLUSTER_PROGRAM_INVALID")
        if self.service_account == "default":
            raise ClusterProcessError("CLUSTER_PROGRAM_INVALID")


class KubernetesProcesses:
    def __init__(self, api_client: client.ApiClient, target: ClusterTarget, program: ClusterProgram):
        configuration = api_client.configuration
        if (not configuration.host.startswith("https://") or configuration.verify_ssl is not True
                or configuration.debug or configuration.proxy or configuration.assert_hostname is False
                or not isinstance(configuration.retries, Retry) or configuration.retries.total != 0):
            raise ClusterProcessError("CLUSTER_TRANSPORT_INVALID")
        self.api_client, self.api = api_client, client.CoreV1Api(api_client)
        self.target, self.program = target, program

    @classmethod
    def in_cluster(cls, target: ClusterTarget, program: ClusterProgram):
        configuration = client.Configuration()
        # Official loader supports projected service-account token refresh.
        config.load_incluster_config(client_configuration=configuration)
        configuration.retries = Retry(total=0, connect=0, read=0, redirect=0, status=0)
        configuration.debug = False
        return cls(client.ApiClient(configuration), target, program)

    async def close(self):
        await asyncio.to_thread(self.api_client.close)

    async def _call(self, method, *args, missing=False, **kwargs):
        try:
            # No automatic SDK retries. A cancelled thread can still finish an
            # in-flight request; the durable launching state retains its quota.
            return await asyncio.to_thread(method, *args, **kwargs, _request_timeout=(5, 15))
        except ApiException as error:
            if missing and error.status == 404:
                return None
            raise ClusterProcessError("CLUSTER_OPERATION_UNCONFIRMED") from None
        except (asyncio.CancelledError, ClusterProcessError):
            raise
        except Exception:
            raise ClusterProcessError("CLUSTER_OPERATION_UNCONFIRMED") from None

    async def verify_target(self):
        for name, uid in (("kube-system", self.target.clusterId), (self.target.namespace, self.target.namespaceUid)):
            namespace = await self._call(self.api.read_namespace, name)
            if (namespace.metadata.uid != uid or namespace.metadata.name != name
                    or namespace.metadata.deletion_timestamp is not None or namespace.status.phase != "Active"):
                raise ClusterProcessError("CLUSTER_TARGET_CHANGED")

    def _bound(self, row: ClusterSnapshot):
        if (row.target != self.target or row.lease.workerId != self.target.workerId
                or row.lease.scope.environment != self.target.environment
                or row.podName != "oa-exec-" + row.lease.leaseId.replace("-", "")):
            raise ClusterProcessError("CLUSTER_BINDING_INVALID")

    def manifest(self, row: ClusterSnapshot) -> dict:
        self._bound(row)
        volumes = [{"name": "worker", "secret": {"secretName": self.program.worker_secret,
                    "defaultMode": 0o440, "items": [{"key": "worker.json", "path": "worker.json"}]}},
                   {"name": "tmp", "emptyDir": {"sizeLimit": "64Mi"}}]
        mounts = [{"name": "worker", "mountPath": "/run/open-agent/worker", "readOnly": True},
                  {"name": "tmp", "mountPath": "/tmp"}]
        if self.program.ca_config_map:
            volumes.append({"name": "certs", "configMap": {"name": self.program.ca_config_map, "defaultMode": 0o444}})
            mounts.append({"name": "certs", "mountPath": "/run/open-agent/certs", "readOnly": True})
        container = {
            "name": "executor", "image": self.target.executorImage, "imagePullPolicy": "IfNotPresent",
            "command": ["/opt/venv/bin/python", "-m", "agent.durable.cluster_child"],
            "env": [{"name": "OPEN_AGENT_LEASE", "value": canonical(row.lease.model_dump()).decode()},
                    {"name": "OPEN_AGENT_POD_UID", "valueFrom": {"fieldRef": {"apiVersion": "v1", "fieldPath": "metadata.uid"}}},
                    {"name": "LANGSMITH_TRACING", "value": "false"},
                    {"name": "LANGCHAIN_TRACING_V2", "value": "false"},
                    {"name": "PYTHONDONTWRITEBYTECODE", "value": "1"}],
            "securityContext": {"runAsNonRoot": True, "runAsUser": 10001, "runAsGroup": 10001,
                                "allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True,
                                "capabilities": {"drop": ["ALL"]}, "seccompProfile": {"type": "RuntimeDefault"}},
            "resources": {"requests": {"cpu": "100m", "memory": "256Mi", "ephemeral-storage": "128Mi"},
                          "limits": {"cpu": "1", "memory": "512Mi", "ephemeral-storage": "256Mi"}},
            "volumeMounts": mounts, "terminationMessagePath": "/dev/termination-log", "terminationMessagePolicy": "File",
        }
        spec = {"containers": [container], "volumes": volumes, "restartPolicy": "Never", "dnsPolicy": "ClusterFirst",
                "schedulingGates": [{"name": SCHEDULING_GATE}],
                "schedulerName": "default-scheduler", "terminationGracePeriodSeconds": 30, "activeDeadlineSeconds": 3610,
                "serviceAccountName": self.program.service_account, "automountServiceAccountToken": False,
                "enableServiceLinks": False, "nodeSelector": {"kubernetes.io/os": "linux", "kubernetes.io/arch": "amd64",
                                                               PREFIX + "executor-pool": self.program.node_pool},
                "securityContext": {"runAsNonRoot": True, "runAsUser": 10001, "runAsGroup": 10001,
                                    "fsGroup": 10001, "fsGroupChangePolicy": "OnRootMismatch",
                                    "seccompProfile": {"type": "RuntimeDefault"}}}
        if self.program.image_pull_secrets:
            spec["imagePullSecrets"] = [{"name": name} for name in self.program.image_pull_secrets]
        binding = {"lease": row.lease.model_dump(), "target": row.target.model_dump(), "supervisor": row.supervisorId,
                   "spec": spec}
        return {"apiVersion": "v1", "kind": "Pod", "metadata": {
            "name": row.podName, "namespace": self.target.namespace, "finalizers": [FINALIZER],
            "labels": {"app.kubernetes.io/name": "open-agent-executor", PREFIX + "lease": row.lease.leaseId,
                       PREFIX + "environment": self.target.environment},
            "annotations": {PREFIX + "binding-sha256": sha256(canonical(binding)).hexdigest()},
        }, "spec": spec}

    def validate_pod(self, row: ClusterSnapshot, pod, *, finalizer_required=True):
        expected = self.manifest(row)
        data = clean(self.api_client.sanitize_for_serialization(pod))
        meta, spec = data.get("metadata", {}), data.get("spec", {})
        try:
            if str(UUID(meta["uid"])) != meta["uid"] or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", meta["resourceVersion"]):
                raise ValueError()
            if (meta.get("name") != row.podName or meta.get("namespace") != self.target.namespace
                    or row.podUid is not None and meta["uid"] != row.podUid or meta.get("ownerReferences")):
                raise ValueError()
            for section in ("labels", "annotations"):
                if any(meta.get(section, {}).get(key) != value for key, value in expected["metadata"][section].items()):
                    raise ValueError()
            if finalizer_required and FINALIZER not in meta.get("finalizers", []):
                raise ValueError()
            # Known API defaults/placement fields only. Unknown admission changes
            # (including sidecars, projected tokens, host mounts) fail closed.
            node_name = spec.pop("nodeName", None)
            if spec.get("schedulingGates") and node_name:
                raise ValueError()
            priority = spec.pop("priority", 0)
            if type(priority) is not int or priority != 0 or spec.pop("preemptionPolicy", "PreemptLowerPriority") != "PreemptLowerPriority":
                raise ValueError()
            if spec.pop("serviceAccount", self.program.service_account) != self.program.service_account:
                raise ValueError()
            for key in ("hostNetwork", "hostPID", "hostIPC", "shareProcessNamespace"):
                if spec.pop(key, False) is not False:
                    raise ValueError()
            for toleration in spec.pop("tolerations", []):
                if (toleration.get("key") not in {"node.kubernetes.io/not-ready", "node.kubernetes.io/unreachable"}
                        or toleration != {"key": toleration["key"], "operator": "Exists", "effect": "NoExecute", "tolerationSeconds": 300}):
                    raise ValueError()
            if not spec.get("schedulingGates") and row.podUid is not None and row.phase in {"observed", "terminated", "not_started", "released"}:
                spec.pop("schedulingGates", None)
                expected["spec"].pop("schedulingGates")
            if spec != expected["spec"]:
                raise ValueError()
        except (KeyError, ValueError, TypeError):
            raise ClusterProcessError("CLUSTER_POD_IDENTITY_MISMATCH") from None
        return pod

    async def inspect(self, row: ClusterSnapshot, *, finalizer_required=True):
        self._bound(row)
        await self.verify_target()
        pod = await self._call(self.api.read_namespaced_pod, row.podName, self.target.namespace, missing=True)
        return None if pod is None else self.validate_pod(row, pod, finalizer_required=finalizer_required)

    async def launch(self, row: ClusterSnapshot):
        if row.phase != "launching" or row.podUid is not None or row.state != "running" or row.leaseState != "held":
            raise ClusterProcessError("CLUSTER_LAUNCH_CONFLICT")
        if await self.inspect(row) is not None:
            raise ClusterProcessError("CLUSTER_LAUNCH_CONFLICT")
        pod = await self._call(self.api.create_namespaced_pod, self.target.namespace, self.manifest(row), field_validation="Strict")
        return self.validate_pod(row, pod)

    async def activate(self, row: ClusterSnapshot):
        """Only the durably observed UID may become schedulable, after spec verification."""
        if row.phase != "observed" or row.podUid is None or row.state != "running" or row.leaseState != "held":
            raise ClusterProcessError("CLUSTER_ACTIVATION_UNAUTHORIZED")
        pod = await self.inspect(row)
        if pod is None or pod.metadata.deletion_timestamp is not None:
            raise ClusterProcessError("CLUSTER_ACTIVATION_UNCONFIRMED")
        if not pod.spec.scheduling_gates:
            return pod
        body = [{"op": "test", "path": "/metadata/uid", "value": row.podUid},
                {"op": "test", "path": "/metadata/resourceVersion", "value": pod.metadata.resource_version},
                {"op": "test", "path": "/spec/schedulingGates", "value": [{"name": SCHEDULING_GATE}]},
                {"op": "remove", "path": "/spec/schedulingGates"}]
        pod = await self._call(self.api.patch_namespaced_pod, row.podName, self.target.namespace,
                               body, _content_type="application/json-patch+json")
        return self.validate_pod(row, pod)

    async def request_stop(self, row: ClusterSnapshot, pod):
        self.validate_pod(row, pod)
        if row.podUid is None or row.state not in {"succeeded", "failed", "cancelled", "timed_out"}:
            raise ClusterProcessError("CLUSTER_STOP_UNFENCED")
        if pod.metadata.deletion_timestamp is None:
            await self.verify_target()
            await self._call(self.api.delete_namespaced_pod, row.podName, self.target.namespace,
                             body=client.V1DeleteOptions(grace_period_seconds=30,
                             preconditions=client.V1Preconditions(uid=row.podUid, resource_version=pod.metadata.resource_version)))

    async def termination(self, row: ClusterSnapshot, pod) -> PodTermination:
        self.validate_pod(row, pod)
        status = pod.status
        if (row.podUid is None or not pod.spec.node_name or status is None or status.phase not in {"Succeeded", "Failed"}
                or status.init_container_statuses or status.ephemeral_container_statuses or len(status.container_statuses or []) != 1):
            raise ClusterProcessError("CLUSTER_TERMINATION_UNCONFIRMED")
        state = status.container_statuses[0]
        terminated = state.state.terminated if state.state else None
        if (state.name != "executor" or state.restart_count != 0 or terminated is None or state.state.running or state.state.waiting
                or not terminated.container_id or terminated.container_id != state.container_id):
            raise ClusterProcessError("CLUSTER_TERMINATION_UNCONFIRMED")
        # PodGC can mark a lost-node Pod Failed. Require actual container exit
        # metadata and a currently identifiable Ready node, never phase alone.
        node = await self._call(self.api.read_node, pod.spec.node_name)
        if (node.metadata.name != pod.spec.node_name or node.metadata.deletion_timestamp is not None
                or not any(condition.type == "Ready" and condition.status == "True" for condition in node.status.conditions or [])):
            raise ClusterProcessError("CLUSTER_NODE_UNCONFIRMED")
        try:
            values = {"podUid": row.podUid, "resourceVersion": pod.metadata.resource_version,
                      "nodeName": pod.spec.node_name, "nodeUid": node.metadata.uid, "podPhase": status.phase,
                      "containerName": "executor", "containerId": terminated.container_id, "exitCode": terminated.exit_code,
                      "startedAt": terminated.started_at.isoformat(), "finishedAt": terminated.finished_at.isoformat()}
            digest = sha256(canonical(values)).hexdigest()
            receipt = PodTermination(id=str(uuid5(UUID(row.lease.leaseId), digest)), evidenceSha256=digest, **values)
            if not receipt.consistent():
                raise ValueError()
            return receipt
        except (AttributeError, TypeError, ValueError):
            raise ClusterProcessError("CLUSTER_TERMINATION_UNCONFIRMED") from None

    def unbound_unstarted(self, row: ClusterSnapshot, pod) -> bool:
        self.validate_pod(row, pod)
        status = pod.status
        return (not pod.spec.node_name
                and (status is None or (status.phase in {None, "Pending", "Failed"}
                    and not status.container_statuses and not status.init_container_statuses
                    and not status.ephemeral_container_statuses and not status.nominated_node_name)))

    def not_started_receipt(self, row: ClusterSnapshot, pod) -> PodNotStarted:
        if (row.podUid is None or row.state not in {"succeeded", "failed", "cancelled", "timed_out"}
                or not self.unbound_unstarted(row, pod) or pod.metadata.deletion_timestamp is None):
            raise ClusterProcessError("CLUSTER_NOT_STARTED_UNCONFIRMED")
        values = {"podUid": row.podUid, "resourceVersion": pod.metadata.resource_version,
                  "deletionTimestamp": pod.metadata.deletion_timestamp.isoformat(),
                  "schedulingGate": SCHEDULING_GATE if pod.spec.scheduling_gates else None,
                  "nodeUnbound": True, "containerStatusesEmpty": True}
        digest = sha256(canonical(values)).hexdigest()
        return PodNotStarted(id=str(uuid5(UUID(row.lease.leaseId), "not-started:" + digest)), evidenceSha256=digest, **values)

    async def finalize(self, row: ClusterSnapshot):
        receipt = row.termination if row.phase == "terminated" else row.notStarted if row.phase == "not_started" else None
        if receipt is None or receipt.podUid != row.podUid:
            raise ClusterProcessError("CLUSTER_RECEIPT_NOT_PERSISTED")
        pod = await self.inspect(row, finalizer_required=False)
        if pod is None:
            return
        # Later metadata changes may change resourceVersion, but never permit a
        # different container/node/exit identity than the durable receipt.
        observed = None
        if FINALIZER in (pod.metadata.finalizers or []):
            if row.phase == "not_started":
                observed = self.not_started_receipt(row, pod)
            else:
                observed = await self.termination(row, pod)
        if observed is not None:
            fields = {"id", "resourceVersion", "evidenceSha256"}
            if row.phase == "not_started":
                fields.add("deletionTimestamp")  # Deletion may be shortened, never undone.
            if observed.model_dump(exclude=fields) != receipt.model_dump(exclude=fields):
                raise ClusterProcessError("CLUSTER_TERMINATION_CHANGED")
        if pod.metadata.deletion_timestamp is None:
            await self.request_stop(row, pod)
            return  # Read a fresh resourceVersion on the next recovery pass.
        finalizers = pod.metadata.finalizers or []
        if FINALIZER in finalizers:
            body = [{"op": "test", "path": "/metadata/uid", "value": row.podUid},
                    {"op": "test", "path": "/metadata/resourceVersion", "value": pod.metadata.resource_version},
                    {"op": "test", "path": "/metadata/finalizers", "value": finalizers},
                    {"op": "replace", "path": "/metadata/finalizers", "value": [value for value in finalizers if value != FINALIZER]}]
            await self._call(self.api.patch_namespaced_pod, row.podName, self.target.namespace,
                             body, _content_type="application/json-patch+json")

    async def absent_after_receipt(self, row: ClusterSnapshot) -> bool:
        if not (row.phase == "terminated" and row.termination is not None or row.phase == "not_started" and row.notStarted is not None):
            raise ClusterProcessError("CLUSTER_RECEIPT_NOT_PERSISTED")
        return await self.inspect(row, finalizer_required=False) is None
