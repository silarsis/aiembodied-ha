"""Tests for the aiembodied conversation agent."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

import custom_components.aiembodied as integration
from custom_components.aiembodied.api_client import AIEmbodiedClientError
from custom_components.aiembodied.const import DATA_RUNTIME, DOMAIN
from homeassistant.components import conversation
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import HomeAssistantError


@dataclass
class _DummyConfigEntries:
    """Minimal config entries manager used for reload tests."""

    async_reload: Any


class _DummyHass(HomeAssistant):
    """Simple Home Assistant substitute for tests."""

    def __init__(self) -> None:
        super().__init__()
        self.config_entries = _DummyConfigEntries(async_reload=self._async_reload)
        self.reloads: list[str] = []
        self.services = _DummyServices()

    async def _async_reload(self, entry_id: str) -> None:
        self.reloads.append(entry_id)


class _DummyServices:
    """Minimal service registry for exercising helper services."""

    def __init__(self) -> None:
        self.handlers: dict[tuple[str, str], tuple[Any, bool]] = {}

    def has_service(self, domain: str, service: str) -> bool:
        return (domain, service) in self.handlers

    def async_register(
        self,
        domain: str,
        service: str,
        handler: Any,
        *,
        supports_response: bool = False,
        schema: Any | None = None,
    ) -> None:
        self.handlers[(domain, service)] = (handler, supports_response)

    async def async_call(
        self,
        domain: str,
        service: str,
        data: dict[str, Any],
        *,
        blocking: bool = False,
        return_response: bool = False,
    ) -> Any:
        handler, supports_response = self.handlers[(domain, service)]
        result = await handler(_SimpleServiceCall(data))
        if return_response and supports_response:
            return result
        return None


class _SimpleServiceCall:
    """Service call shim matching the attributes used by the handler."""

    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data


class _MockConfigEntry:
    """Config entry stub providing the hooks used by the integration."""

    def __init__(self, entry_id: str, data: dict[str, Any]) -> None:
        self.entry_id = entry_id
        self.data = data
        self._update_listener: Any = None
        self._unload_callbacks: list[Any] = []

    def add_update_listener(self, listener: Any) -> Any:
        self._update_listener = listener

        def _remove() -> None:
            self._update_listener = None

        return _remove

    def async_on_unload(self, callback: Any) -> None:
        self._unload_callbacks.append(callback)


class _StubClient:
    """Fake API client capturing payloads sent by the agent."""

    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        self.requests: list[dict[str, Any]] = []

    async def async_post_json(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.requests.append(payload)
        return self.response


class _FailingClient(_StubClient):
    """Client that raises an API error."""

    async def async_post_json(self, payload: dict[str, Any]) -> dict[str, Any]:
        raise AIEmbodiedClientError("boom")


def _make_config(**overrides: Any) -> integration.IntegrationConfig:
    """Helper to construct integration configs for conversation tests."""

    base = dict(
        endpoint="https://example.invalid/api",
        auth_token=None,
        headers={},
        exposure=[],
        throttle=None,
        batching=False,
        routing={},
    )
    base.update(overrides)
    return integration.IntegrationConfig(**base)


@pytest.mark.asyncio
async def test_conversation_agent_handles_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    """The conversation agent sends structured payloads and returns responses."""

    hass = _DummyHass()
    entry = _MockConfigEntry(
        "entry-1",
        {
            "endpoint": "https://example.invalid/api",
            "auth_token": "token",
            "headers": {"X-Test": "1"},
            "exposure": ["light.kitchen"],
            "throttle": 30,
            "batching": True,
            "routing": {"pipeline": "assist"},
        },
    )

    client = _StubClient({"reply": "Hi there!", "conversation_id": "remote-123"})
    monkeypatch.setattr(integration, "_create_client", lambda hass, config: client)

    await integration.async_setup(hass, {})
    await integration.async_setup_entry(hass, entry)

    runtime_wrapper = hass.data[DOMAIN][entry.entry_id]
    runtime = runtime_wrapper[DATA_RUNTIME]
    agent = conversation.async_get_agent(hass, entry.entry_id)

    assert agent is runtime.agent

    result = await agent.async_handle(
        conversation.ConversationInput(
            text="Turn on the lights",
            conversation_id="local-456",
            language="en",
            device_id="device-1",
            context=Context(id="context-1", user_id="user-1"),
        )
    )

    assert result.conversation_id == "remote-123"
    assert result.response.text == "Hi there!"
    assert result.response.language == "en"

    payload = client.requests.pop()
    assert payload["input"] == {
        "text": "Turn on the lights",
        "conversation_id": "local-456",
        "language": "en",
        "device_id": "device-1",
    }
    assert payload["config"] == {
        "exposure": ["light.kitchen"],
        "batching": True,
        "throttle": 30,
        "routing": {"pipeline": "assist"},
    }
    assert payload["context"] == {"id": "context-1", "user_id": "user-1"}

    assert await integration.async_unload_entry(hass, entry)
    assert conversation.async_get_agent(hass, entry.entry_id) is None


@pytest.mark.asyncio
async def test_conversation_agent_wraps_client_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    """Client errors are surfaced as conversation errors."""

    hass = _DummyHass()
    entry = _MockConfigEntry("entry-2", {"endpoint": "https://example.invalid/api"})

    client = _FailingClient({})
    monkeypatch.setattr(integration, "_create_client", lambda hass, config: client)

    await integration.async_setup(hass, {})
    await integration.async_setup_entry(hass, entry)

    agent = conversation.async_get_agent(hass, entry.entry_id)
    with pytest.raises(conversation.ConversationError):
        await agent.async_handle(conversation.ConversationInput(text="Hello"))


@pytest.mark.asyncio
async def test_send_conversation_turn_service_returns_agent_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Service helper forwards requests to the configured conversation agent."""

    hass = _DummyHass()
    entry = _MockConfigEntry(
        "entry-3",
        {
            "endpoint": "https://example.invalid/api",
            "exposure": ["light.living_room"],
        },
    )

    client = _StubClient({"text": "Lights set", "conversation_id": "conv-789"})
    monkeypatch.setattr(integration, "_create_client", lambda hass, config: client)

    await integration.async_setup(hass, {})
    await integration.async_setup_entry(hass, entry)

    response = await hass.services.async_call(
        integration.DOMAIN,
        integration.SERVICE_SEND_CONVERSATION_TURN,
        {
            "entry_id": entry.entry_id,
            "text": "Set the lights",
            "conversation_id": "local-1",
            "language": "en",
            "device_id": "device-9",
            "context_id": "ctx-1",
            "context_user_id": "user-2",
        },
        blocking=True,
        return_response=True,
    )

    assert response == {
        "conversation_id": "conv-789",
        "response": {
            "text": "Lights set",
            "language": "en",
            "data": {"text": "Lights set", "conversation_id": "conv-789"},
        },
    }

    payload = client.requests.pop()
    assert payload["input"] == {
        "text": "Set the lights",
        "conversation_id": "local-1",
        "language": "en",
        "device_id": "device-9",
    }
    assert payload["config"] == {"exposure": ["light.living_room"], "batching": False}
    assert payload["context"] == {"id": "ctx-1", "user_id": "user-2"}


@pytest.mark.asyncio
async def test_send_conversation_turn_service_requires_active_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Calling the helper without a configured entry raises an error."""

    hass = _DummyHass()
    await integration.async_setup(hass, {})

    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            integration.DOMAIN,
            integration.SERVICE_SEND_CONVERSATION_TURN,
            {"entry_id": "missing", "text": "hi"},
            blocking=True,
            return_response=True,
        )
def test_conversation_agent_metadata_and_text_coercion() -> None:
    """Metadata properties and text coercion helpers are exercised."""

    config = _make_config(exposure=["light.kitchen"], routing={"pipeline": "assist"})
    agent = integration.AIEmbodiedConversationAgent(_StubClient({"reply": "ok"}), config)

    assert agent.supported_languages == {"*"}
    assert agent.attribution == {"name": "Embodied AI"}
    assert agent._serialize_context(None) is None
    assert agent._serialize_context(Context(id="ctx")) == {"id": "ctx"}
    assert agent._coerce_text({"text": "  hi  "}) == "hi"


@pytest.mark.asyncio
async def test_conversation_agent_errors_when_text_missing() -> None:
    """Missing textual responses raise conversation errors."""

    config = _make_config()
    agent = integration.AIEmbodiedConversationAgent(_StubClient({}), config)

    with pytest.raises(conversation.ConversationError):
        await agent.async_handle(conversation.ConversationInput(text="Hello"))


@pytest.mark.asyncio
async def test_conversation_agent_conversation_id_fallback() -> None:
    """Conversation id falls back to the local value when not provided."""

    config = _make_config()
    client = _StubClient({"reply": "hi"})
    agent = integration.AIEmbodiedConversationAgent(client, config)

    result = await agent.async_handle(
        conversation.ConversationInput(
            text="Hello",
            conversation_id="local-id",
            language="en",
        )
    )

    assert result.conversation_id == "local-id"
