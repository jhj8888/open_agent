import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('registry_record', Path(__file__).resolve().parents[2] /
    'deploy/platform/images/record_registry_images.py')
record = importlib.util.module_from_spec(spec)
spec.loader.exec_module(record)


def test_registry_digest_must_be_attached_to_the_verified_image():
    value = 'registry.internal.example/open-agent/api@sha256:' + 'a' * 64
    expected = 'sha256:' + 'b' * 64
    context = 'c' * 64
    image = {'Id': expected, 'RepoDigests': [value], 'Config': {'Labels': {
        'io.open-agent.owner': 'open_agent.platform.images.v1', 'io.open-agent.context-sha256': context}}}
    result = record.verify_image(value, expected, context, lambda _: image)
    assert result['digest'] == 'sha256:' + 'a' * 64
    for changed in [dict(image, Id='sha256:' + 'd' * 64), dict(image, RepoDigests=[]),
                    dict(image, Config={'Labels': {}})]:
        with pytest.raises(ValueError):
            record.verify_image(value, expected, context, lambda _: changed)


@pytest.mark.parametrize('value', ['sha256:' + 'a' * 64, 'registry.private/api:latest',
                                  'docker.io/open-agent/api@sha256:' + 'a' * 64,
                                  'ghcr.io/open-agent/api@sha256:' + 'a' * 64])
def test_unpinned_config_only_and_public_registry_references_are_rejected(value):
    with pytest.raises(ValueError):
        record.reference(value)
