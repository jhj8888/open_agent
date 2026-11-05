"""Prepare public, offline inputs for the pinned OpenSandbox Kubernetes provider.

This does not install a runtime, open task admission, or certify a CNI. Secrets
are supplied separately through the official server API-key environment variable.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from pathlib import Path
import re

import yaml

SERVER_DIGEST = '68ca0212a2749b2c73096ce2ec0264455c64442c45f81007db442f52bf84c9d1'
EXECD_DIGEST = 'e11fdd6fa641329a1fe5298e71c8dd2afca742674fe48bc855b2ff6715512829'
CONTROLLER_DIGEST = '7299b94ab1382be4adb4fd41209ea60e16f66fcd963af64345b1106ac434957e'
FINALIZER = 'open-agent.io/sandbox-termination'
CR_FINALIZER = 'open-agent.io/sandbox-workload'
TEMPLATE_PATH = '/etc/open-agent-sandbox/batchsandbox-template.yaml'


@dataclass(frozen=True)
class Profile:
    environment: str
    registry: str
    server_image: str
    execd_image: str
    controller_image: str
    skill_image: str
    runtime_class: str
    image_pull_secret: str
    maximum_sandboxes: int = 16

    def __post_init__(self):
        label = r'[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?'
        if self.environment not in {'test', 'prod'}:
            raise ValueError('invalid_environment')
        if (not re.fullmatch(rf'{label}(?:\.{label})*(?::[1-9][0-9]{{0,4}})?', self.registry)
                or len(self.registry.split(':')[0]) > 253
                or (':' in self.registry and int(self.registry.rsplit(':', 1)[1]) > 65535)
                or self.registry.split(':')[0] in {'docker.io', 'index.docker.io', 'registry-1.docker.io', 'ghcr.io', 'quay.io', 'm.daocloud.io'}):
            raise ValueError('private_registry_required')
        for value, expected in [(self.server_image, SERVER_DIGEST), (self.execd_image, EXECD_DIGEST),
                                (self.controller_image, CONTROLLER_DIGEST), (self.skill_image, None)]:
            if not re.fullmatch(re.escape(self.registry) + r'/[a-z0-9][a-z0-9./_-]*@sha256:[a-f0-9]{64}', value):
                raise ValueError('immutable_private_image_required')
            if expected and not value.endswith('@sha256:' + expected):
                raise ValueError('upstream_image_digest_mismatch')
        if not re.fullmatch(label, self.runtime_class) or not re.fullmatch(label, self.image_pull_secret):
            raise ValueError('invalid_runtime_or_secret_name')
        if type(self.maximum_sandboxes) is not int or not 1 <= self.maximum_sandboxes <= 256:
            raise ValueError('invalid_sandbox_limit')

    @property
    def namespace(self):
        return 'open-agent-sandboxes-' + self.environment

    @property
    def control_namespace(self):
        return 'open-agent-' + self.environment


def documents(profile: Profile) -> dict[str, bytes]:
    """Use supported template fields; runtime replacement is tested upstream."""
    template = {
        'apiVersion': 'sandbox.opensandbox.io/v1alpha1', 'kind': 'BatchSandbox',
        'metadata': {'finalizers': [CR_FINALIZER]},
        'spec': {'template': {'metadata': {'finalizers': [FINALIZER]}, 'spec': {
            'runtimeClassName': profile.runtime_class,
            'restartPolicy': 'Never', 'terminationGracePeriodSeconds': 30, 'activeDeadlineSeconds': 3600,
            'automountServiceAccountToken': False, 'serviceAccountName': 'open-agent-sandbox',
            'enableServiceLinks': False, 'hostNetwork': False, 'hostPID': False, 'hostIPC': False,
            'dnsPolicy': 'None', 'dnsConfig': {'nameservers': ['127.0.0.1']},
            'nodeSelector': {'open-agent.io/sandbox-pool': 'kata', 'kubernetes.io/os': 'linux'},
            'imagePullSecrets': [{'name': profile.image_pull_secret}],
            'securityContext': {'runAsUser': 0, 'runAsGroup': 0, 'seccompProfile': {'type': 'RuntimeDefault'}},
            # Runtime main-container fields replace the template's list. Only
            # securityContext and extra mounts survive the provider's own merge.
            'containers': [{'name': 'sandbox', 'securityContext': {
                'privileged': False, 'allowPrivilegeEscalation': False, 'readOnlyRootFilesystem': True,
                'capabilities': {'drop': ['ALL'], 'add': ['CHOWN', 'DAC_OVERRIDE', 'FOWNER', 'SETGID', 'SETUID']},
            }, 'volumeMounts': [
                {'name': name, 'mountPath': path} for name, path in
                [('workspace', '/workspace'), ('skills', '/skills'), ('tmp', '/tmp')]]
            }],
            'volumes': [
                {'name': 'workspace', 'emptyDir': {'sizeLimit': '256Mi'}},
                {'name': 'skills', 'emptyDir': {'sizeLimit': '32Mi'}},
                {'name': 'tmp', 'emptyDir': {'medium': 'Memory', 'sizeLimit': '64Mi'}},
            ],
        }}}}
    server = f'''# OpenSandbox 1.1.0; API key via OPENSANDBOX_SERVER_API_KEY Secret.
[server]
host = "0.0.0.0"
port = 8080
max_sandbox_timeout_seconds = 3600
[proxy]
resolve_internal = true
[runtime]
type = "kubernetes"
execd_image = {json.dumps(profile.execd_image)}
execd_run_as_init = true
[kubernetes]
namespace = {json.dumps(profile.namespace)}
workload_provider = "batchsandbox"
batchsandbox_template_file = {json.dumps(TEMPLATE_PATH)}
image_pull_policy = "IfNotPresent"
[kubernetes.execd_init_resources]
requests = {{cpu = "25m", memory = "32Mi", ephemeral-storage = "32Mi"}}
limits = {{cpu = "250m", memory = "128Mi", ephemeral-storage = "128Mi"}}
[secure_runtime]
type = "kata"
k8s_runtime_class = {json.dumps(profile.runtime_class)}
[ingress]
mode = "direct"
[store]
type = "sqlite"
path = "/var/lib/opensandbox/opensandbox.db"
[renew_intent]
enabled = false
'''
    network = {'apiVersion': 'networking.k8s.io/v1', 'kind': 'NetworkPolicy',
        'metadata': {'name': 'open-agent-sandbox-boundary', 'namespace': profile.namespace},
        'spec': {'podSelector': {}, 'policyTypes': ['Ingress', 'Egress'], 'egress': [],
            'ingress': [{'from': [{
                'namespaceSelector': {'matchLabels': {'kubernetes.io/metadata.name': profile.control_namespace}},
                'podSelector': {'matchLabels': {'app.kubernetes.io/name': 'open-agent-sandbox-server',
                                               'open-agent.io/environment': profile.environment}},
            }], 'ports': [{'port': 44772, 'protocol': 'TCP'}]}]}}
    namespace = {'apiVersion': 'v1', 'kind': 'Namespace', 'metadata': {'name': profile.namespace,
        'labels': {'open-agent.io/environment': profile.environment, 'open-agent.io/sandbox-boundary': 'kata'}}}
    account = {'apiVersion': 'v1', 'kind': 'ServiceAccount',
        'metadata': {'name': 'open-agent-sandbox', 'namespace': profile.namespace},
        'automountServiceAccountToken': False}
    quota = {'apiVersion': 'v1', 'kind': 'ResourceQuota',
        'metadata': {'name': 'open-agent-sandbox-limits', 'namespace': profile.namespace},
        'spec': {'hard': {'pods': str(profile.maximum_sandboxes),
            'count/batchsandboxes.sandbox.opensandbox.io': str(profile.maximum_sandboxes),
            'requests.cpu': str(profile.maximum_sandboxes), 'limits.cpu': str(profile.maximum_sandboxes),
            'requests.memory': f'{profile.maximum_sandboxes * 512}Mi', 'limits.memory': f'{profile.maximum_sandboxes * 512}Mi',
            'requests.ephemeral-storage': f'{profile.maximum_sandboxes * 512}Mi',
            'limits.ephemeral-storage': f'{profile.maximum_sandboxes * 512}Mi'}}}
    result = {name: yaml.safe_dump(value, sort_keys=False).encode() for name, value in {
        'batchsandbox-template.yaml': template, 'network-policy.yaml': network,
        'namespace.yaml': namespace, 'service-account.yaml': account, 'resource-quota.yaml': quota}.items()}
    result['server.toml'] = server.encode()
    manifest = {'schema': 1, 'profile': asdict(profile), 'upstream_commit': 'b00c96b0a89c07fc18409fcfe72d170b4c96c8a7',
        'sdk': {'network_enforcement': 'cluster-cni', 'secure_access': False, 'use_server_proxy': True,
                'resources': {'cpu': '1', 'memory': '512Mi', 'ephemeral-storage': '512Mi'},
                'per_attempt_execd_token': True},
        'task_admission_enabled': False,
        'files': {name: {'sha256': sha256(data).hexdigest(), 'bytes': len(data)} for name, data in result.items()}}
    result['manifest.json'] = (json.dumps(manifest, indent=2) + '\n').encode()
    return result


def prepare(configuration: Path, output: Path):
    profile = Profile(**json.loads(configuration.read_bytes()))
    payload = documents(profile)
    output.mkdir(parents=True, exist_ok=False)
    for name, content in payload.items():
        (output / name).write_bytes(content)
    print(json.dumps({'files': len(payload), 'namespace': profile.namespace,
        'manifest_sha256': sha256(payload['manifest.json']).hexdigest(), 'task_admission_enabled': False}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--configuration', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    prepare(args.configuration, args.output)
