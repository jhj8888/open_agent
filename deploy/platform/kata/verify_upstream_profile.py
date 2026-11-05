"""Run inside the pinned official server image; no API server or sandbox is used.

Exercises official config loading, request validation and BatchSandbox template
merging. The Kubernetes write is captured explicitly, never sent to a cluster.
"""
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
from importlib.metadata import version
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from fastapi import HTTPException
from opensandbox_server.api.schema import CreateSandboxRequest, ImageSpec
from opensandbox_server.config import load_config
from opensandbox_server.services.k8s.batchsandbox_provider import BatchSandboxProvider
from opensandbox_server.services.k8s.kubernetes_service import KubernetesSandboxService
from opensandbox_server.services.k8s.create_helpers import _build_create_workload_context

ROOT = Path('/etc/open-agent-sandbox')


class CapturedKubernetes:
    def __init__(self):
        self.calls = []

    def create_custom_object(self, **kwargs):
        self.calls.append(deepcopy(kwargs))
        result = deepcopy(kwargs['body'])
        result['metadata']['uid'] = str(uuid4())
        return result


def main():
    upstream = json.loads((ROOT/'upstream.json').read_bytes())
    package_version = version('opensandbox-server')
    assert package_version == upstream['server_package_metadata_version']
    for name, expected in upstream['files'].items():
        assert sha256(Path(import_module('opensandbox_server.'+name).__file__).read_bytes()).hexdigest() == expected
    manifest = json.loads((ROOT/'manifest.json').read_bytes())
    for name, item in manifest['files'].items():
        data = (ROOT/name).read_bytes()
        assert len(data) == item['bytes'] and sha256(data).hexdigest() == item['sha256']
    profile = manifest['profile']
    app = load_config(ROOT/'server.toml')
    assert app.runtime.type == 'kubernetes' and app.secure_runtime.type == 'kata'
    assert app.ingress.mode == 'direct' and app.proxy.resolve_internal is True
    assert app.server.api_key is None  # Secret injection is intentionally absent in this isolated test.
    identifier = str(uuid4())
    request = CreateSandboxRequest.model_validate({
        'image': {'uri': profile['skill_image']}, 'entrypoint': ['/bin/bash','-c','exec sleep infinity'],
        'timeout': 600, 'resourceLimits': manifest['sdk']['resources'],
        'env': {'EXECD_ACCESS_TOKEN': 'synthetic-execd-contract-token-with-no-live-authority'},
        'metadata': {'oa.platform': 'open-agent', 'oa.environment': profile['environment'],
            'oa.tenant': 'a'*52, 'oa.run': str(uuid4()), 'oa.attempt': '1', 'oa.lease': str(uuid4())},
        'secureAccess': False, 'extensions': {}, 'volumes': [],
    })
    # Prove direct ingress rejects the old gateway-only flag using actual upstream code.
    service = SimpleNamespace(ingress_config=app.ingress)
    KubernetesSandboxService._ensure_secure_access_support(service, request)
    try:
        KubernetesSandboxService._ensure_secure_access_support(service, request.model_copy(update={'secure_access': True}))
    except HTTPException as error:
        assert error.status_code == 400
    else:
        raise AssertionError('Expected direct-ingress secureAccess rejection')

    capture = CapturedKubernetes()
    provider = BatchSandboxProvider(capture, app)
    def unused_token_factory():
        raise AssertionError('This profile must not request gateway/egress credentials')
    context = _build_create_workload_context(app,request,identifier,datetime.now(timezone.utc),
        unused_token_factory,unused_token_factory)
    provider.create_workload(identifier, app.kubernetes.namespace, ImageSpec(uri=profile['skill_image']),
        request.entrypoint, context.sandbox_env, context.resource_limits, context.labels,
        context.expires_at, app.runtime.execd_image, annotations=context.annotations,
        extensions={}, volumes=[], egress_settings=context.egress_settings)
    assert len(capture.calls) == 1
    call = capture.calls[0]
    assert call['group'] == 'sandbox.opensandbox.io' and call['version'] == 'v1alpha1' and call['plural'] == 'batchsandboxes'
    cr = call['body'];pod = cr['spec']['template']['spec']
    assert cr['metadata']['name'] == identifier and cr['metadata']['namespace'] == app.kubernetes.namespace
    assert cr['metadata']['finalizers'] == ['open-agent.io/sandbox-workload']
    assert cr['spec']['template']['metadata']['finalizers'] == ['open-agent.io/sandbox-termination']
    assert cr['spec']['replicas'] == 1 and not any(k in cr['spec'] for k in ['poolRef','taskTemplate','shardPatches'])
    assert cr['metadata']['labels'] == context.labels == cr['spec']['template']['metadata']['labels']
    assert all(context.labels[k] == v for k,v in request.metadata.items())
    assert pod['runtimeClassName'] == profile['runtime_class'] and pod['restartPolicy'] == 'Never'
    assert pod['automountServiceAccountToken'] is False and pod['serviceAccountName'] == 'open-agent-sandbox'
    assert pod['activeDeadlineSeconds'] == 3600 and pod['terminationGracePeriodSeconds'] == 30
    assert not any(pod[k] for k in ['hostNetwork','hostPID','hostIPC','enableServiceLinks'])
    assert pod['nodeSelector'] == {'open-agent.io/sandbox-pool':'kata','kubernetes.io/os':'linux'}
    assert pod['imagePullSecrets'] == [{'name':profile['image_pull_secret']}]
    assert pod['securityContext'] == {'runAsUser':0,'runAsGroup':0,'seccompProfile':{'type':'RuntimeDefault'}}
    assert pod['dnsPolicy'] == 'None' and pod['dnsConfig'] == {'nameservers':['127.0.0.1']}
    assert [c['name'] for c in pod['containers']] == ['sandbox']
    assert [c['name'] for c in pod['initContainers']] == ['execd-installer']
    main = pod['containers'][0];init = pod['initContainers'][0]
    assert main['image'] == profile['skill_image'] and init['image'] == profile['execd_image']
    assert main['resources']['limits'] == manifest['sdk']['resources']
    assert init['resources']['limits'] == {'cpu':'250m','memory':'128Mi','ephemeral-storage':'128Mi'}
    assert main['command'] == ['/opt/opensandbox/bootstrap.sh',*request.entrypoint]
    assert main['securityContext'] == {'privileged':False,'allowPrivilegeEscalation':False,
        'readOnlyRootFilesystem':True,'capabilities':{'drop':['ALL'],'add':['CHOWN','DAC_OVERRIDE','FOWNER','SETGID','SETUID']}}
    env = {entry['name']:entry['value'] for entry in main['env']}
    assert env == {**request.env,'OPENSANDBOX_ID':identifier,'EXECD_INIT':'1','EXECD':'/opt/opensandbox/execd'}
    assert {v['name'] for v in pod['volumes']} == {'opensandbox-bin','workspace','skills','tmp'}
    assert all(set(v) == {'name','emptyDir'} for v in pod['volumes'])
    assert {m['mountPath'] for m in main['volumeMounts']} == {'/opt/opensandbox','/workspace','/skills','/tmp'}
    assert all('valueFrom' not in entry for entry in main['env'])
    # Keep the generated token-free shape for independent API/schema validation.
    main['env'] = [{**entry,'value':'[SYNTHETIC-REDACTED]'} if entry['name']=='EXECD_ACCESS_TOKEN' else entry for entry in main['env']]
    print(json.dumps({'event':'upstream_profile_verified',
        'profile_manifest_sha256':sha256((ROOT/'manifest.json').read_bytes()).hexdigest(),
        'upstream_umbrella_release':upstream['umbrella_release'],'upstream_source_hashes_verified':True,
        'server_package_metadata_version':package_version,'actual_provider_used':True,'actual_config_and_request_models_used':True,
        'kubernetes_writes_captured_not_sent':len(capture.calls),'direct_secure_access_rejected':True,
        'generated_workload':cr,'kata_or_cni_verified':False}),flush=True)


if __name__ == '__main__':
    main()
