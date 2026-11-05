"""Actual Helm rendering; CEL/RBAC behavior needs the separate real API fixture."""
import os
from pathlib import Path
import subprocess

import pytest
import yaml

from test_platform_chart import HELM, ROOT

CHART = ROOT / 'helm/open-agent-execution'


def values(environment='test'):
    value = yaml.safe_load((CHART / 'examples' / (environment + '.yaml')).read_bytes())
    value['image'] = {'repository': 'registry.internal.invalid/open-agent/worker', 'digest': 'sha256:' + 'a' * 64}
    return value


def render(tmp_path, data, namespace=None, release='fixture', action='template'):
    config = tmp_path / 'execution-values.yaml'
    config.write_text(yaml.safe_dump(data), encoding='utf8')
    args = [HELM, action]
    args += [release, str(CHART), '--kube-version', '1.36.6'] if action == 'template' else [str(CHART), '--strict']
    args += ['--namespace', namespace or data.get('namespace', 'open-agent-test'), '-f', str(config)]
    environment = {**os.environ, **{key: str(tmp_path / key) for key in ['HELM_CACHE_HOME', 'HELM_CONFIG_HOME', 'HELM_DATA_HOME']}}
    return subprocess.run(args, capture_output=True, text=True, env=environment, timeout=60)


@pytest.mark.parametrize('environment', ['test', 'prod'])
def test_supervisor_permissions_credentials_and_executor_networks_are_separate(tmp_path, environment):
    result = render(tmp_path, values(environment))
    assert result.returncode == 0, result.stderr
    docs = [d for d in yaml.safe_load_all(result.stdout) if d]
    assert not {'Secret', 'Ingress', 'Node', 'PersistentVolume', 'DaemonSet'} & {d['kind'] for d in docs}
    role = next(d for d in docs if d['kind'] == 'Role')
    assert role['metadata']['namespace'] == 'open-agent-executors-' + environment
    assert role['rules'] == [{'apiGroups': [''], 'resources': ['pods'], 'verbs': ['get', 'create', 'patch', 'delete']}]
    cluster = next(d for d in docs if d['kind'] == 'ClusterRole')
    assert cluster['rules'] == [{'apiGroups': [''], 'resources': ['namespaces'], 'resourceNames': ['kube-system', 'open-agent-executors-' + environment], 'verbs': ['get']},
        {'apiGroups': [''], 'resources': ['nodes'], 'verbs': ['get']}]
    for binding in [d for d in docs if d['kind'].endswith('RoleBinding')]:
        assert binding['subjects'] == [{'kind': 'ServiceAccount', 'name': 'fixture-execution-supervisor', 'namespace': 'open-agent-' + environment}]
    accounts = [d for d in docs if d['kind'] == 'ServiceAccount']
    assert len(accounts) == 2 and all(not s['automountServiceAccountToken'] for s in accounts)
    assert all(not s.get('imagePullSecrets') for s in accounts)
    deployment = next(d for d in docs if d['kind'] == 'Deployment')
    pod = deployment['spec']['template']['spec']
    assert pod['securityContext']['runAsUser'] == 10001
    assert pod['automountServiceAccountToken'] is False
    assert pod['containers'][0]['command'] == ['/opt/venv/bin/python', '-m', 'agent.durable.cluster_service']
    assert pod['containers'][0]['securityContext']['readOnlyRootFilesystem'] is True
    assert all('hostPath' not in v for v in pod['volumes'])
    assert {v['secret']['secretName'] for v in pod['volumes'] if 'secret' in v} == {'open-agent-' + environment + '-supervisor', 'open-agent-' + environment + '-temporal'}
    assert not any('worker' in str(v) for v in pod['volumes'])
    projection = next(v for v in pod['volumes'] if 'projected' in v)['projected']
    assert projection['sources'][0]['serviceAccountToken']['expirationSeconds'] == 3600
    policies = [d for d in docs if d['kind'] == 'NetworkPolicy']
    deny = next(d for d in policies if d['metadata']['name'].endswith('executor-deny'))
    assert deny['spec'] == {'podSelector': {}, 'policyTypes': ['Ingress', 'Egress'], 'ingress': [], 'egress': []}
    executor = next(d for d in policies if d['metadata']['name'].endswith('executor-egress'))
    destinations = executor['spec']['egress']
    assert len(destinations) == 3  # DNS, control, model; no K8s or Temporal.
    assert all('ipBlock' not in peer for rule in destinations for peer in rule['to'])
    for policy in policies:
        for rule in policy['spec'].get('egress', []):
            for peer in rule.get('to', []):
                if 'namespaceSelector' in peer:
                    assert peer['podSelector']['matchLabels']
    admission = next(d for d in docs if d['kind'] == 'ValidatingAdmissionPolicy')
    assert admission['spec']['failurePolicy'] == 'Fail'
    binding = next(d for d in docs if d['kind'] == 'ValidatingAdmissionPolicyBinding')
    assert binding['spec']['validationActions'] == ['Deny']
    assert binding['spec']['matchResources']['namespaceSelector']['matchLabels']['kubernetes.io/metadata.name'] == 'open-agent-executors-' + environment
    assert len([d for d in docs if d['kind'] == 'PodDisruptionBudget']) == (1 if environment == 'prod' else 0)


@pytest.mark.parametrize('case', ['defaults', 'mutable_image', 'public_api', 'metadata_api', 'foreign_secret', 'foreign_registry', 'foreign_peer', 'no_model_route', 'inline_secret', 'executor_namespace', 'singleton_prod', 'blocking_pdb', 'k8s_all_ports'])
def test_incomplete_or_cross_environment_inputs_are_rejected(tmp_path, case):
    data = values('prod')
    if case == 'defaults': data = {}
    if case == 'mutable_image': data['image']['digest'] = 'latest'
    if case == 'public_api': data['network']['kubernetes']['addresses'] = ['8.8.8.8']
    if case == 'metadata_api': data['network']['kubernetes']['addresses'] = ['169.254.169.254']
    if case == 'foreign_secret': data['supervisor']['existingSecret'] = 'open-agent-test-supervisor'
    if case == 'foreign_registry': data['executor']['imagePullSecrets'] = ['open-agent-test-registry']
    if case == 'foreign_peer': data['network']['control']['podLabels']['open-agent.io/environment'] = 'test'
    if case == 'no_model_route': data['network']['models'] = []
    if case == 'inline_secret': data['supervisor']['serviceKey'] = 'synthetic-unaccepted-configuration'
    if case == 'executor_namespace': data['executorNamespace'] = 'open-agent-prod'
    if case == 'singleton_prod': data['supervisor']['replicas'] = 1
    if case == 'blocking_pdb': data['supervisor']['disruptionBudget']['minAvailable'] = 2
    if case == 'k8s_all_ports': data['network']['kubernetes']['ports'] = []
    assert render(tmp_path, data).returncode != 0, case


def test_release_namespace_and_global_names_cannot_collide(tmp_path):
    assert render(tmp_path, values(), namespace='open-agent-prod').returncode != 0
    result = render(tmp_path, values('prod'), release='a' * 53)
    assert result.returncode == 0, result.stderr
    assert all(len(d['metadata']['name']) <= 63 for d in yaml.safe_load_all(result.stdout) if d)


@pytest.mark.parametrize('environment', ['test', 'prod'])
def test_execution_chart_helm_lint(tmp_path, environment):
    result = render(tmp_path, values(environment), action='lint')
    assert result.returncode == 0, result.stderr + result.stdout
