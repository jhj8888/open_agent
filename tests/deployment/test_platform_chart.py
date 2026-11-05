"""Render the real chart with Helm; synthetic image digests are test fixtures only."""
import os
from pathlib import Path
import shutil
import subprocess

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
CHART = ROOT / 'helm/open-agent-platform'
HELM = os.environ.get('HELM_BINARY') or shutil.which('helm') or str(ROOT / '.local/helm-4.3.1/helm.exe')


def values(environment):
    data = yaml.safe_load((CHART / 'examples' / (environment + '.yaml')).read_text())
    data['images'] = {c: {'repository': 'registry.internal.invalid/open-agent/' + c,
                          'digest': 'sha256:' + character * 64} for c, character in [('api', 'a'), ('web', 'b')]}
    return data


def helm(tmp_path, data, action='template', namespace=None, release='fixture'):
    assert Path(HELM).is_file() or shutil.which(HELM), 'Install the pinned Helm CLI before running deployment checks'
    incoming = tmp_path / 'values.yaml'
    incoming.write_text(yaml.safe_dump(data), encoding='utf8')
    environment = {**os.environ, **{key: str(tmp_path / key) for key in
                   ['HELM_CACHE_HOME', 'HELM_CONFIG_HOME', 'HELM_DATA_HOME']}}
    args = [HELM, action]
    if action == 'template':
        args += [release, str(CHART), '--kube-version', '1.35.0']
    else:
        args += [str(CHART), '--strict']
    args += ['--namespace', namespace or data.get('namespace', 'open-agent-test'), '-f', str(incoming)]
    return subprocess.run(args, capture_output=True, text=True, env=environment, timeout=30)


@pytest.mark.parametrize('environment', ['test', 'prod'])
def test_chart_renders_hardened_workloads_and_private_dependencies(tmp_path, environment):
    data = values(environment)
    lint = helm(tmp_path, data, 'lint')
    assert lint.returncode == 0, lint.stdout + lint.stderr
    result = helm(tmp_path, data)
    assert result.returncode == 0, result.stderr
    documents = [d for d in yaml.safe_load_all(result.stdout) if d]
    assert all(d['metadata']['namespace'] == f'open-agent-{environment}' for d in documents)
    assert not {'Secret', 'ClusterRole', 'ClusterRoleBinding', 'Ingress', 'DaemonSet', 'PersistentVolume'} & {d['kind'] for d in documents}
    deployments = [d for d in documents if d['kind'] == 'Deployment']
    assert len(deployments) == 2
    for deployment in deployments:
        pod = deployment['spec']['template']['spec']
        assert pod['automountServiceAccountToken'] is False
        assert pod['securityContext']['seccompProfile']['type'] == 'RuntimeDefault'
        assert not any(v.get('hostPath') for v in pod['volumes'])
        assert pod['securityContext']['runAsNonRoot'] is True
        for container in pod['containers']:
            assert '@sha256:' in container['image']
            assert container['securityContext'] == {'allowPrivilegeEscalation': False,
                   'readOnlyRootFilesystem': True, 'capabilities': {'drop': ['ALL']}}
            assert {'startupProbe', 'readinessProbe', 'livenessProbe'} <= container.keys()
            assert set(container['resources']['requests']) == {'cpu', 'memory'}
            assert set(container['resources']['limits']) == {'cpu', 'memory'}
            secrets = [e['valueFrom']['secretKeyRef'] for e in container.get('env', []) if 'secretKeyRef' in e.get('valueFrom', {})]
            if container['name'] == 'web':
                assert not secrets
            else:
                assert secrets and {s['name'] for s in secrets} == {f'open-agent-{environment}-control'}
    for service in [d for d in documents if d['kind'] == 'Service']:
        assert service['spec']['type'] == 'ClusterIP'
    policies = [d for d in documents if d['kind'] == 'NetworkPolicy']
    deny = next(d for d in policies if d['metadata']['name'].endswith('-default-deny'))
    assert deny['spec']['policyTypes'] == ['Ingress', 'Egress']
    assert deny['spec']['ingress'] == deny['spec']['egress'] == []
    for policy in policies:
        for direction, key in [('ingress', 'from'), ('egress', 'to')]:
            for rule in policy['spec'].get(direction, []):
                for peer in rule.get(key, []):
                    if 'namespaceSelector' in peer:
                        assert peer.get('podSelector', {}).get('matchLabels'), 'Namespace and pod selectors must be ANDed'
    config = next(d['data'] for d in documents if d['kind'] == 'ConfigMap')
    assert config['PLATFORM_RUN_ADMISSION_ENABLED'] == 'false'
    assert config['PLATFORM_RUN_ADMISSION_ENVIRONMENTS'] == environment
    assert config['MODEL_PUBLIC_DEMO_ORIGINS'] == '[]'
    assert 'header_up -X-Platform-*' in config['Caddyfile']
    assert 'respond "Not found" 404' in config['Caddyfile']
    assert len([d for d in documents if d['kind'] == 'PodDisruptionBudget']) == (2 if environment == 'prod' else 0)
    assert len([d for d in documents if d['kind'] == 'HorizontalPodAutoscaler']) == (1 if environment == 'prod' else 0)


def test_default_values_cannot_be_accidentally_installed(tmp_path):
    result = helm(tmp_path, {})
    assert result.returncode != 0
    assert 'schema' in result.stderr.lower()


@pytest.mark.parametrize('case', ['mutable_image', 'public_network', 'metadata_network', 'default_route',
                                 'foreign_secret', 'foreign_peer', 'empty_gateway_selector', 'missing_dependency',
                                 'admission_enabled', 'extra_env', 'untrusted_https_identity', 'missing_memory',
                                 'prod_singleton', 'blocking_pdb', 'invalid_hpa'])
def test_unsafe_or_incomplete_configuration_is_rejected(tmp_path, case):
    data = values('prod')
    if case == 'mutable_image': data['images']['api']['digest'] = 'latest'
    if case in ['public_network', 'metadata_network', 'default_route']:
        data['network']['dependencies'][0] = {'component': 'api', 'purpose': 'database', 'ports': [3306],
            'cidr': {'public_network': '8.8.8.8/32', 'metadata_network': '169.254.169.254/32', 'default_route': '0.0.0.0/0'}[case]}
    if case == 'foreign_secret': data['existingSecret'] = 'open-agent-test-control'
    if case == 'foreign_peer': data['network']['dependencies'][0]['podLabels']['open-agent.io/environment'] = 'test'
    if case == 'empty_gateway_selector': data['network']['gateway']['podLabels'] = {}
    if case == 'missing_dependency': data['network']['dependencies'][0]['purpose'] = 'model'
    if case == 'admission_enabled': data['executionAdmission'] = True
    if case == 'extra_env': data['extraEnv'] = {'INJECTED_SECRET': 'never-accepted'}
    if case == 'untrusted_https_identity': data['identity']['upstream'] = 'https://identity.private:8443'
    if case == 'missing_memory': data['api']['resources'] = {'limits': {'memory': None}}
    if case == 'prod_singleton': data['web']['replicas'] = 1
    if case == 'blocking_pdb': data['api']['disruptionBudget']['minAvailable'] = 2
    if case == 'invalid_hpa': data['api']['autoscaling']['maxReplicas'] = 1
    result = helm(tmp_path, data)
    assert result.returncode != 0, case


def test_release_namespace_cannot_cross_environments(tmp_path):
    result = helm(tmp_path, values('prod'), namespace='open-agent-test')
    assert result.returncode != 0
    assert 'namespace' in result.stderr.lower()


def test_valid_explicit_private_ip_dependency(tmp_path):
    data = values('test')
    data['network']['dependencies'][0] = {'component': 'api', 'purpose': 'database', 'cidr': '10.20.30.40/32', 'ports': [3306]}
    result = helm(tmp_path, data)
    assert result.returncode == 0, result.stderr


def test_long_valid_release_name_stays_within_kubernetes_resource_name_limits(tmp_path):
    result = helm(tmp_path, values('prod'), release='a' * 53)
    assert result.returncode == 0, result.stderr
    assert all(len(d['metadata']['name']) <= 63 for d in yaml.safe_load_all(result.stdout) if d)
