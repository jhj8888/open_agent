"""Regression tests for protocol sanitization at Flow service/cache boundaries."""

import json
from unittest.mock import MagicMock, patch

from workflow.cache import flow as flow_cache
from workflow.consts.tenant_publish_matrix import ReleaseStatus
from workflow.domain.entities.flow import FlowUpdate, PublishInput
from workflow.domain.models.flow import Flow
from workflow.service import flow_service, publish_service


def _legacy_protocol() -> dict:
    return {
        "apiKey": "valid-model-key",
        "data": {
            "nodes": [
                {
                    "sandbox": {
                        "enabled": True,
                        "workflowId": "flow-1",
                        "apiKey": "sandbox-secret",
                        "artifactUploadToken": "artifact-secret",
                    }
                }
            ]
        },
    }


def test_save_and_update_store_sanitized_copies_without_mutating_input() -> None:
    protocol = _legacy_protocol()
    flow = Flow(name="workflow", app_id="app-1", data=protocol)
    app_info = MagicMock(actual_source=0)
    session = MagicMock()

    with patch.object(flow_service, "get_id", return_value=101):
        saved = flow_service.save(flow, app_info, session, MagicMock())

    assert saved.data["apiKey"] == "valid-model-key"
    assert saved.data["data"]["nodes"][0]["sandbox"] == {
        "enabled": True,
        "workflowId": "flow-1",
    }
    assert protocol["data"]["nodes"][0]["sandbox"]["apiKey"] == "sandbox-secret"

    updated_protocol = _legacy_protocol()
    db_flow = Flow(id=101, group_id=101, data={})
    flow_service.update(session, db_flow, FlowUpdate(data=updated_protocol))
    assert db_flow.data["data"]["nodes"][0]["sandbox"] == {
        "enabled": True,
        "workflowId": "flow-1",
    }
    assert updated_protocol["data"]["nodes"][0]["sandbox"]["apiKey"]


def test_get_sanitizes_database_result_before_return_and_cache() -> None:
    db_flow = Flow(id=101, group_id=101, data=_legacy_protocol())
    session = MagicMock()
    session.query.return_value.filter_by.return_value.first.return_value = db_flow

    with (
        patch.object(flow_cache, "get_flow_by_id", return_value=None),
        patch.object(flow_cache, "set_flow_by_id") as cache_set,
    ):
        result = flow_service.get("101", session, MagicMock())

    assert result.data["data"]["nodes"][0]["sandbox"] == {
        "enabled": True,
        "workflowId": "flow-1",
    }
    cached = cache_set.call_args.args[1]
    assert cached.data == result.data


def test_get_fails_closed_for_invalid_database_protocol_text() -> None:
    db_flow = Flow(id=101, group_id=101)
    db_flow.data = '{"sandbox":'  # type: ignore[assignment]
    db_flow.release_data = "not-json"  # type: ignore[assignment]
    session = MagicMock()
    session.query.return_value.filter_by.return_value.first.return_value = db_flow

    with (
        patch.object(flow_cache, "get_flow_by_id", return_value=None),
        patch.object(flow_cache, "set_flow_by_id"),
    ):
        result = flow_service.get("101", session, MagicMock())

    assert result.data == {}
    assert result.release_data == {}


def test_get_fails_closed_for_list_root_and_decodes_double_encoded_object() -> None:
    db_flow = Flow(id=101, group_id=101)
    db_flow.data = "[]"  # type: ignore[assignment]
    db_flow.release_data = json.dumps(json.dumps(_legacy_protocol()))  # type: ignore[assignment]
    session = MagicMock()
    session.query.return_value.filter_by.return_value.first.return_value = db_flow

    with (
        patch.object(flow_cache, "get_flow_by_id", return_value=None),
        patch.object(flow_cache, "set_flow_by_id"),
    ):
        result = flow_service.get("101", session, MagicMock())

    assert result.data == {}
    assert result.release_data["data"]["nodes"][0]["sandbox"] == {
        "enabled": True,
        "workflowId": "flow-1",
    }


def test_get_latest_published_sanitizes_cached_release_protocol() -> None:
    cached = Flow(
        id=201,
        group_id=101,
        release_data=_legacy_protocol(),
        release_status=ReleaseStatus.PUBLISH.value,
    )

    with patch.object(flow_cache, "get_flow_by_flow_id_latest", return_value=cached):
        result = flow_service.get_latest_published("101", MagicMock(), MagicMock())

    assert result.release_data["data"]["nodes"][0]["sandbox"] == {
        "enabled": True,
        "workflowId": "flow-1",
    }


def test_cache_uses_v3_namespace_and_never_receives_unsanitized_flow() -> None:
    cache_service = MagicMock()
    source = Flow(id=101, group_id=101, data=_legacy_protocol())

    with patch.object(flow_cache, "get_cache_service", return_value=cache_service):
        flow_cache.set_flow_by_id("101", source)

    assert cache_service.set.call_args.kwargs["key"] == "workflow:flow_info:v3:101"
    cached = cache_service.set.call_args.kwargs["value"]
    assert cached is not source
    assert cached.data["data"]["nodes"][0]["sandbox"] == {
        "enabled": True,
        "workflowId": "flow-1",
    }
    assert source.data["data"]["nodes"][0]["sandbox"]["apiKey"] == "sandbox-secret"


def test_cache_write_fails_closed_for_invalid_protocol_text() -> None:
    cache_service = MagicMock()
    source = Flow(id=101, group_id=101)
    source.data = '{"sandbox":'  # type: ignore[assignment]

    with patch.object(flow_cache, "get_cache_service", return_value=cache_service):
        flow_cache.set_flow_by_id("101", source)

    cached = cache_service.set.call_args.kwargs["value"]
    assert cached.data == {}
    assert source.data == '{"sandbox":'


def test_publish_sync_sanitizes_data_and_release_data_without_aliasing() -> None:
    db_flow = Flow(id=101, group_id=101, data=_legacy_protocol())
    publish_input = PublishInput(
        flow_id="101",
        release_status=ReleaseStatus.PUBLISH.value,
    )

    publish_service._update_flow_data(db_flow, publish_input)

    sandbox = db_flow.data["data"]["nodes"][0]["sandbox"]
    assert sandbox == {"enabled": True, "workflowId": "flow-1"}
    assert db_flow.release_data == db_flow.data
    assert db_flow.release_data is not db_flow.data
