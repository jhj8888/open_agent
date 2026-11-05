import importlib.util
import json
from pathlib import Path
import sys
import tomllib

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('kata_profile', ROOT / 'deploy/platform/kata/prepare_profile.py')
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


def configuration(environment='test'):
    return dict(environment=environment, registry='registry.internal',
        server_image='registry.internal/opensandbox/server@sha256:'+module.SERVER_DIGEST,
        execd_image='registry.internal/opensandbox/execd@sha256:'+module.EXECD_DIGEST,
        controller_image='registry.internal/opensandbox/controller@sha256:'+module.CONTROLLER_DIGEST,
        skill_image='registry.internal/open-agent/skill@sha256:'+'a'*64,
        runtime_class='kata-qemu', image_pull_secret='open-agent-registry')


@pytest.mark.parametrize('environment',['test','prod'])
def test_profile_closes_network_and_preserves_separate_environment_boundary(environment):
    profile=module.Profile(**configuration(environment));files=module.documents(profile)
    network=yaml.safe_load(files['network-policy.yaml'])
    assert network['metadata']['namespace']=='open-agent-sandboxes-'+environment
    assert network['spec']['podSelector']=={} and network['spec']['egress']==[]
    ingress=network['spec']['ingress']
    assert ingress==[{'from':[{'namespaceSelector':{'matchLabels':{'kubernetes.io/metadata.name':'open-agent-'+environment}},
        'podSelector':{'matchLabels':{'app.kubernetes.io/name':'open-agent-sandbox-server','open-agent.io/environment':environment}}}],
        'ports':[{'port':44772,'protocol':'TCP'}]}]
    server=tomllib.loads(files['server.toml'].decode())
    assert server['runtime']['type']=='kubernetes' and server['secure_runtime']['type']=='kata'
    assert server['ingress']['mode']=='direct' and 'egress' not in server
    assert server['server']['max_sandbox_timeout_seconds']==3600 and 'api_key' not in server['server']
    assert server['kubernetes']['namespace']==profile.namespace
    template=yaml.safe_load(files['batchsandbox-template.yaml'])
    assert template['metadata']['finalizers']==[module.CR_FINALIZER]
    assert template['spec']['template']['metadata']['finalizers']==[module.FINALIZER]
    pod=template['spec']['template']['spec']
    assert pod['automountServiceAccountToken'] is False and pod['restartPolicy']=='Never'
    assert pod['runtimeClassName']=='kata-qemu' and pod['activeDeadlineSeconds']==3600
    assert all('emptyDir' in v and 'sizeLimit' in v['emptyDir'] for v in pod['volumes'])
    assert not any(pod[k] for k in ['hostNetwork','hostPID','hostIPC','enableServiceLinks'])
    security=pod['containers'][0]['securityContext']
    assert security['readOnlyRootFilesystem'] is True and security['capabilities']['drop']==['ALL']
    assert security['privileged'] is False and security['allowPrivilegeEscalation'] is False
    assert set(security['capabilities']['add'])=={'CHOWN','DAC_OVERRIDE','FOWNER','SETGID','SETUID'}
    manifest=json.loads(files['manifest.json'])
    assert manifest['task_admission_enabled'] is False
    assert manifest['sdk']['network_enforcement']=='cluster-cni'


@pytest.mark.parametrize('key,value',[
    ('environment','dev'),('registry','docker.io'),('registry','ghcr.io'),('registry','registry.internal/path'),
    ('registry','registry.internal:65536'),('registry','registry..internal'),('registry','registry.internal:0'),
    ('server_image','registry.internal/opensandbox/server:latest'),
    ('server_image','registry.internal/opensandbox/server@sha256:'+'0'*64),
    ('execd_image','foreign.internal/execd@sha256:'+module.EXECD_DIGEST),
    ('skill_image','registry.internal/open-agent/skill:latest'),
    ('runtime_class','kata/escape'),('image_pull_secret','bad\nsecret'),
    ('maximum_sandboxes',True),('maximum_sandboxes',0),('maximum_sandboxes',257),
])
def test_unpinned_or_mismatched_profile_is_rejected_before_output(key,value):
    data=configuration();data[key]=value
    with pytest.raises(ValueError):module.Profile(**data)


def test_prepare_never_overwrites_an_existing_delivery_and_manifest_covers_payload(tmp_path):
    config=tmp_path/'profile.json';config.write_text(json.dumps(configuration()))
    destination=tmp_path/'delivery';module.prepare(config,destination)
    manifest=json.loads((destination/'manifest.json').read_bytes())
    assert set(manifest['files'])=={p.name for p in destination.iterdir()}-{'manifest.json'}
    for name,entry in manifest['files'].items():
        raw=(destination/name).read_bytes()
        assert module.sha256(raw).hexdigest()==entry['sha256'] and len(raw)==entry['bytes']
    before=(destination/'manifest.json').read_bytes()
    with pytest.raises(FileExistsError):module.prepare(config,destination)
    assert (destination/'manifest.json').read_bytes()==before
