"""Record real private-registry manifest digests after load/tag/push by an operator.

No login, pull or push is performed. Requires already-local references that Docker
identifies as the exact verified images. Never uses a config ID as a pull digest.
"""
import argparse
import json
from pathlib import Path
import re
import subprocess


def reference(value):
    match = re.fullmatch(r'([a-z0-9][a-z0-9.-]+(?::[0-9]{1,5})?/[a-z0-9][a-z0-9._/-]*)@(sha256:[a-f0-9]{64})', value)
    if not match:
        raise ValueError('registry_reference_requires_manifest_digest')
    host = match[1].split('/')[0].split(':')[0]
    if host in {'docker.io', 'index.docker.io', 'ghcr.io', 'quay.io', 'm.daocloud.io', 'registry.k8s.io'}:
        raise ValueError('private_registry_required')
    return match[1], match[2]


def verify_image(value, expected_id, context, inspect):
    repository, digest = reference(value)
    image = inspect(value)
    if image['Id'] != expected_id or value not in (image.get('RepoDigests') or []):
        raise ValueError('imported_image_does_not_match_verified_build')
    labels = image['Config'].get('Labels') or {}
    if labels.get('io.open-agent.owner') != 'open_agent.platform.images.v1' or labels.get('io.open-agent.context-sha256') != context:
        raise ValueError('imported_image_provenance_mismatch')
    return {'repository': repository, 'digest': digest}


def docker_inspect(value):
    result = subprocess.run(['docker', 'image', 'inspect', value], capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise ValueError('image_not_locally_imported')
    return json.loads(result.stdout)[0]


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--build-report', type=Path, required=True)
    parser.add_argument('--api', required=True)
    parser.add_argument('--web', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        build = json.loads(args.build_report.read_text())
        values = {'images': {component: verify_image(getattr(args, component), build['images'][component]['local_image_id'],
                            build['context_sha256'], docker_inspect) for component in ['api', 'web']}}
        with args.output.open('x', encoding='utf8') as stream:
            json.dump(values, stream, indent=2)
            stream.write('\n')
        print(json.dumps({'event': 'private_registry_images_recorded', 'components': ['api', 'web']}))
    except Exception as error:
        print(json.dumps({'event': 'registry_image_record_failed', 'error_type': type(error).__name__}))
        raise SystemExit(1) from None
