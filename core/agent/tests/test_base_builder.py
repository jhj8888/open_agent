"""Test BaseApiBuilder class"""

import os
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import urlparse

import pytest
from common.otlp import sid as sid_module
from common.otlp.trace.span import Span

from agent.domain.models.providers import ModelHandle
from agent.infra.app_auth import MaasAuth
from agent.service.builder.base_builder import BaseApiBuilder
from agent.service.plugin.base import BasePlugin
from agent.service.plugin.skill_resource_security import (
    SKILL_RESOURCE_TRUSTED_BUCKET_ENV,
    SKILL_RESOURCE_TRUSTED_ORIGIN_ENV,
)

_SIGV4_QUERY = (
    "X-Amz-Algorithm=AWS4-HMAC-SHA256&"
    "X-Amz-Credential=test%2F20260824%2Fus-east-1%2Fs3%2Faws4_request&"
    "X-Amz-Date=20260824T000000Z&X-Amz-Expires=300&"
    "X-Amz-SignedHeaders=host&X-Amz-Signature=" + "a" * 64
)
_SKILL_URL = f"https://example.com/console-oss/skill-files/user/skill.md?{_SIGV4_QUERY}"


@dataclass
class _DummySidGen:
    """Simple sid generator for testing environment."""

    value: str = "test-sid"

    def gen(self) -> str:  # pragma: no cover - only for testing environment
        return self.value


@pytest.fixture(autouse=True)
def _setup_test_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Automatically inject environment fixes for all tests.

    - Ensure `sid_generator2` is initialized to avoid `Span` construction failure.
    """
    monkeypatch.setenv("MODEL_ALLOWED_ORIGINS", '["https://api.test.com"]')
    # Initialize sid generator to avoid Span throwing "sid_generator2 is not initialized"
    if sid_module.sid_generator2 is None:
        sid_module.sid_generator2 = _DummySidGen()  # type: ignore[assignment]


class TestBaseApiBuilder:
    """Test BaseApiBuilder class"""

    @pytest.fixture
    def span(self) -> Span:
        """Create Span instance for testing"""
        return Span(app_id="test_app", uid="test_uid")

    @pytest.fixture
    def builder(self, span: Span) -> BaseApiBuilder:
        """Create Builder instance for testing"""
        return BaseApiBuilder(app_id="test_app", uid="test_uid", span=span)

    @pytest.mark.asyncio
    async def test_build_plugins_empty(self, builder: BaseApiBuilder) -> None:
        """Test building plugins (empty list)"""
        plugins = await builder.build_plugins([], [], [], [])
        assert plugins == []

    @pytest.mark.asyncio
    async def test_build_plugins_with_tool_ids(self, builder: BaseApiBuilder) -> None:
        """Test building plugins (tool IDs)"""
        mock_plugin = MagicMock(spec=BasePlugin)
        mock_plugin.name = "test_tool"
        mock_plugin.typ = "tool"
        mock_plugin.schema_template = "tool schema"

        with patch(
            "agent.service.builder.base_builder.LinkPluginFactory"
        ) as mock_factory:
            mock_factory.return_value.gen = AsyncMock(return_value=[mock_plugin])

            plugins = await builder.build_plugins(["tool1"], [], [], [])

            assert len(plugins) > 0

    @pytest.mark.asyncio
    async def test_build_plugins_with_mcp_server_ids(
        self, builder: BaseApiBuilder
    ) -> None:
        """Test building plugins (MCP server IDs)"""
        mock_plugin = MagicMock(spec=BasePlugin)
        mock_plugin.name = "mcp_plugin"
        mock_plugin.typ = "mcp"
        mock_plugin.schema_template = "mcp schema"

        with patch(
            "agent.service.builder.base_builder.McpPluginFactory"
        ) as mock_factory:
            mock_factory.return_value.gen = AsyncMock(return_value=[mock_plugin])

            plugins = await builder.build_plugins([], ["mcp1"], [], [])

            assert len(plugins) > 0

    @pytest.mark.asyncio
    async def test_build_plugins_with_workflow_ids(
        self, builder: BaseApiBuilder
    ) -> None:
        """Test building plugins (workflow IDs)"""
        mock_plugin = MagicMock(spec=BasePlugin)
        mock_plugin.name = "workflow_plugin"
        mock_plugin.typ = "workflow"
        mock_plugin.schema_template = "workflow schema"

        with patch(
            "agent.service.builder.base_builder.WorkflowPluginFactory"
        ) as mock_factory:
            mock_factory.return_value.gen = AsyncMock(return_value=[mock_plugin])

            plugins = await builder.build_plugins([], [], [], ["workflow1"])

            assert len(plugins) > 0

    @pytest.mark.asyncio
    async def test_build_plugins_with_skills(
        self, builder: BaseApiBuilder, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test building plugins (skills)"""
        monkeypatch.setenv(SKILL_RESOURCE_TRUSTED_ORIGIN_ENV, "https://example.com")
        monkeypatch.setenv(SKILL_RESOURCE_TRUSTED_BUCKET_ENV, "console-oss")
        plugins = await builder.build_plugins(
            [],
            [],
            [],
            [],
            skills=[
                {
                    "skill_id": "skill-1",
                    "name": "ui-ux-pro-max",
                    "description": "Design reference skill",
                    "download_url": _SKILL_URL,
                }
            ],
        )

        assert len(plugins) == 2
        assert [plugin.typ for plugin in plugins] == ["skill", "skill"]
        assert [plugin.name for plugin in plugins] == [
            "read_skill_skill-1",
            "run_skill_skill-1",
        ]

    @pytest.mark.asyncio
    async def test_build_plugins_filters_empty_mcp_urls(
        self, builder: BaseApiBuilder
    ) -> None:
        """Test filtering empty MCP URLs when building plugins"""
        with patch(
            "agent.service.builder.base_builder.McpPluginFactory"
        ) as mock_factory:
            mock_factory.return_value.gen = AsyncMock(return_value=[])

            await builder.build_plugins([], [], ["", "  ", "valid_url"], [])

            # Verify only valid URLs are processed
            mock_factory.assert_called_once()
            call_args = mock_factory.call_args
            mcp_urls = call_args[1]["mcp_server_urls"]
            assert "valid_url" in mcp_urls
            assert "" not in mcp_urls
            assert "  " not in mcp_urls

    @pytest.mark.asyncio
    async def test_query_maas_sk(self, builder: BaseApiBuilder) -> None:
        """Test querying MaaS SK"""
        mock_sk = "test_key:test_secret"

        with patch.object(MaasAuth, "sk", return_value=mock_sk):
            sk = await builder.query_maas_sk("test_app", "test_model")

            assert sk == mock_sk

    @pytest.mark.asyncio
    async def test_create_model_with_api_key(self, builder: BaseApiBuilder) -> None:
        """Test creating model (with API key provided)"""
        model = await builder.create_model(
            app_id="test_app",
            model_name="test_model",
            base_url="https://api.test.com",
            api_key="provided_key",
        )

        assert isinstance(model, ModelHandle)
        assert model.name == "test_model"
        # Verify provided API key is used
        assert model.model.openai_api_key.get_secret_value() == "provided_key"
        await model.aclose()

    @pytest.mark.asyncio
    async def test_create_model_does_not_record_api_credentials(
        self, builder: BaseApiBuilder
    ) -> None:
        credential = "tenant-key:tenant-secret-must-not-reach-telemetry"

        with patch.object(builder.span, "add_info_events") as add_info_events:
            model = await builder.create_model(
                app_id="test_app",
                model_name="test_model",
                base_url="https://api.test.com",
                api_key=credential,
            )

        recorded_events = repr(add_info_events.call_args_list)
        assert credential not in recorded_events
        assert "model_auth_configured" in recorded_events
        await model.aclose()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("provider", ["anthropic", "google"])
    async def test_private_platform_rejects_protocol_without_safe_transport(self, builder: BaseApiBuilder, provider: str) -> None:
        with pytest.raises(ValueError, match="OpenAI-compatible"):
            await builder.create_model(app_id="test_app", model_name="model", base_url="https://api.test.com", provider=provider, api_key="provided_key")

    @pytest.mark.asyncio
    async def test_missing_credential_never_calls_public_maas(self, builder: BaseApiBuilder) -> None:
        with patch.object(BaseApiBuilder, "query_maas_sk", new_callable=AsyncMock) as fallback:
            with pytest.raises(ValueError, match="fallback is disabled"):
                await builder.create_model(app_id="test_app", model_name="model", base_url="https://api.test.com", api_key="")
            fallback.assert_not_called()

    @pytest.mark.asyncio
    async def test_create_model_normalize_base_url_chat_completions(
        self, builder: BaseApiBuilder
    ) -> None:
        """Test normalizing base_url (contains /chat/completions)"""
        model = await builder.create_model(
            app_id="test_app",
            model_name="test_model",
            base_url="https://api.test.com/chat/completions",
            api_key="test_key",
        )

        # Verify base_url is normalized by AsyncOpenAI (removes /chat/completions)
        assert "/chat/completions" not in str(model.model.openai_api_base)
        # Verify URL hostname using proper URL parsing to avoid security issues
        parsed_url = urlparse(str(model.model.openai_api_base))
        assert parsed_url.netloc == "api.test.com"
        await model.aclose()

    @pytest.mark.asyncio
    async def test_create_model_normalize_base_url_completions(
        self, builder: BaseApiBuilder
    ) -> None:
        """Test normalizing base_url (contains /completions)"""
        model = await builder.create_model(
            app_id="test_app",
            model_name="test_model",
            base_url="https://api.test.com/completions",
            api_key="test_key",
        )

        # Verify base_url is normalized by AsyncOpenAI (removes /completions)
        assert "/completions" not in str(model.model.openai_api_base)
        # Verify URL hostname using proper URL parsing to avoid security issues
        parsed_url = urlparse(str(model.model.openai_api_base))
        assert parsed_url.netloc == "api.test.com"
        await model.aclose()

    @pytest.mark.asyncio
    async def test_create_model_ssl_verify_enabled(
        self, builder: BaseApiBuilder
    ) -> None:
        """Test creating model (SSL verification enabled)"""
        with patch.dict(os.environ, {"SKIP_SSL_VERIFY": "false"}):
            model = await builder.create_model(
                app_id="test_app",
                model_name="test_model",
                base_url="https://api.test.com",
                api_key="test_key",
            )

            assert isinstance(model, ModelHandle)
        await model.aclose()
        # Verify HTTP client is configured with SSL verification

    @pytest.mark.asyncio
    async def test_legacy_skip_ssl_flag_cannot_disable_private_model_verification(
        self, builder: BaseApiBuilder
    ) -> None:
        """The old global flag cannot override the private platform TLS transport."""
        with patch.dict(os.environ, {"SKIP_SSL_VERIFY": "true"}):
            model = await builder.create_model(
                app_id="test_app",
                model_name="test_model",
                base_url="https://api.test.com",
                api_key="test_key",
            )

            assert isinstance(model, ModelHandle)
        await model.aclose()
        import ssl
        from agent.domain.models.endpoint_policy import PinnedModelTransport
        transport = model.http_client._transport
        assert isinstance(transport, PinnedModelTransport)
        assert transport.verify.verify_mode == ssl.CERT_REQUIRED
        assert transport.verify.check_hostname
