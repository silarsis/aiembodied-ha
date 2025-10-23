"""Tests for the aiembodied integration setup module."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from typing import Any, Mapping

import pytest

import custom_components.aiembodied as integration
from custom_components.aiembodied.const import (
    DATA_RUNTIME,
    DOMAIN,
    OPTIONS_AUTONOMY_PAUSED,
)
from homeassistant.exceptions import HomeAssistantError


class _DummyConfigEntries:
    """Minimal config entries manager for testing reload behavior."""

    def __init__(self, hass: "_DummyHass") -> None:
        self._hass = hass
        self.forwarded: list[tuple[object, list[object]]] = []
        self.unloaded: list[tuple[object, list[object]]] = []
        self.updated: list[dict[str, Any]] = []

    async def async_reload(self, entry_id: str) -> None:
        await self._hass._async_reload(entry_id)

    async def async_forward_entry_setups(
        self, entry: object, platforms: Iterable[object]
    ) -> None:  # noqa: ANN001 - signature mirrors Home Assistant
        self.forwarded.append((entry, list(platforms)))

    async def async_unload_platforms(
        self, entry: object, platforms: Iterable[object]
    ) -> bool:  # noqa: ANN001 - signature mirrors Home Assistant
        self.unloaded.append((entry, list(platforms)))
        return True

    async def async_update_entry(
        self,
        entry: "_MockConfigEntry",
        *,
        data: Mapping[str, Any] | None = None,
        options: Mapping[str, Any] | None = None,
    ) -> None:
        if data is not None:  # pragma: no cover - not exercised in these tests
            entry.data = dict(data)
        if options is not None:
            entry.options = dict(options)
            self.updated.append(dict(options))
        listener = entry._update_listener
        if listener is not None:
            await listener(self._hass, entry)


class _DummyHass:
    """Simplified Home Assistant core object for unit tests."""

    def __init__(self) -> None:
        self.data: dict[str, dict[str, object]] = {}
        self.config_entries = _DummyConfigEntries(self)
        self.reload_requests: list[str] = []
        self.services = _DummyServices()
        self.bus = _DummyBus()

    async def _async_reload(self, entry_id: str) -> None:
        self.reload_requests.append(entry_id)


class _DummyBus:
    """Minimal event bus capturing fired events."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def async_fire(self, event_type: str, event_data: dict[str, Any]) -> None:
        self.events.append((event_type, event_data))


class _DummyServices:
    """Service registry stub capturing registrations."""

    def __init__(self) -> None:
        self.registered: dict[tuple[str, str], dict[str, object]] = {}
        self.calls: list[dict[str, Any]] = []
        self.async_call_handler: Callable[..., Any] | None = None

    def has_service(self, domain: str, service: str) -> bool:
        return (domain, service) in self.registered

    def async_register(
        self,
        domain: str,
        service: str,
        handler: object,
        *,
        schema: object | None = None,
        supports_response: bool = False,
    ) -> None:
        self.registered[(domain, service)] = {
            "handler": handler,
            "schema": schema,
            "supports_response": supports_response,
        }

    async def async_call(
        self,
        domain: str,
        service: str,
        service_data: dict[str, Any] | None = None,
        *,
        blocking: bool = False,
        return_response: bool = False,
        target: dict[str, Any] | None = None,
        context: object | None = None,
    ) -> Any:
        call = {
            "domain": domain,
            "service": service,
            "service_data": service_data or {},
            "blocking": blocking,
            "return_response": return_response,
            "target": target,
            "context": context,
        }
        self.calls.append(call)
        if self.async_call_handler is None:
            return {}
        result = self.async_call_handler(
            domain,
            service,
            service_data or {},
            target=target,
            blocking=blocking,
            return_response=return_response,
            context=context,
        )
        if hasattr(result, "__await__"):
            return await result  # type: ignore[return-value]
        return result


class _BaseExposure:
    """Common stub implementing the exposure controller interface."""

    async def async_setup(self) -> None:
        return None

    async def async_shutdown(self) -> None:
        return None

    async def async_set_paused(self, paused: bool) -> None:  # noqa: ARG002
        return None


class _MockConfigEntry:
    """Small stub mimicking the ConfigEntry interface used by the integration."""

    def __init__(self, entry_id: str, data: dict[str, object]) -> None:
        self.entry_id = entry_id
        self.data = data
        self.options: dict[str, Any] = {}
        self._update_listener: Callable[[object], Awaitable[None]] | None = None
        self._unload_callbacks: list[Callable[[], None]] = []

    def add_update_listener(
        self, listener: Callable[[object], Awaitable[None]]
    ) -> Callable[[], None]:
        self._update_listener = listener

        def _remove() -> None:
            self._update_listener = None

        return _remove

    def async_on_unload(self, callback: Callable[[], None]) -> None:
        self._unload_callbacks.append(callback)


@pytest.mark.asyncio
async def test_async_get_options_flow_returns_handler() -> None:
    """The options flow factory returns a configured handler instance."""

    entry = _MockConfigEntry("entry-options", {"endpoint": "https://example.invalid/api"})
    handler = await integration.async_get_options_flow(entry)  # type: ignore[arg-type]
    assert isinstance(handler, integration.AIEmbodiedOptionsFlowHandler)
    assert handler._config_entry is entry  # type: ignore[attr-defined]


def test_async_get_clientsession_delegates(monkeypatch: pytest.MonkeyPatch) -> None:
    """The helper proxies to Home Assistant's aiohttp client session helper."""

    sentinel = object()

    def _fake_get_clientsession(hass: object) -> object:  # noqa: ANN001
        assert hass == "hass"
        return sentinel

    monkeypatch.setattr(
        integration.aiohttp_client,
        "async_get_clientsession",
        _fake_get_clientsession,
    )

    assert integration._async_get_clientsession("hass") is sentinel


@pytest.mark.asyncio
async def test_async_setup_entry_stores_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    """Setting up an entry creates runtime data and registers reload listener."""

    hass = _DummyHass()
    entry = _MockConfigEntry(
        "entry-1",
        {
            "endpoint": "https://example.invalid/api",
            "auth_token": "secret",
            "headers": {"X-Test": "1"},
        },
    )

    created_clients: list[object] = []

    class _DummyClient:
        pass

    controllers: list[_DummyExposure] = []

    class _DummyExposure:
        def __init__(
            self,
            hass_obj: object,
            client_obj: object,
            config_obj: object,
            entry_id: str,
            autonomy: object,
        ) -> None:  # noqa: ANN001
            self.hass = hass_obj
            self.client = client_obj
            self.config = config_obj
            self.entry_id = entry_id
            self.setup_calls = 0
            self.shutdown_calls = 0
            self.pause_calls: list[bool] = []
            controllers.append(self)

        async def async_setup(self) -> None:
            self.setup_calls += 1

        async def async_shutdown(self) -> None:
            self.shutdown_calls += 1

        async def async_set_paused(self, paused: bool) -> None:
            self.pause_calls.append(paused)

    def _fake_session_factory(hass_obj: object) -> object:  # noqa: ANN001 - signature for monkeypatch
        return object()

    def _fake_client_factory(session: object, config: object) -> _DummyClient:  # noqa: ANN001
        client = _DummyClient()
        created_clients.append(client)
        return client

    monkeypatch.setattr(integration, "_async_get_clientsession", _fake_session_factory)
    def _fake_client_constructor(*args: object, **kwargs: object) -> _DummyClient:
        return _fake_client_factory(*args, **kwargs)

    monkeypatch.setattr(integration, "AIEmbodiedClient", _fake_client_constructor)
    monkeypatch.setattr(integration, "ExposureController", _DummyExposure)

    assert await integration.async_setup(hass, {})
    assert await integration.async_setup_entry(hass, entry)

    runtime_wrapper = hass.data[DOMAIN][entry.entry_id]
    runtime = runtime_wrapper[DATA_RUNTIME]
    assert runtime.config.endpoint == "https://example.invalid/api"
    assert runtime.config.auth_token == "secret"
    assert runtime.config.headers == {"X-Test": "1"}
    assert isinstance(runtime.client, _DummyClient)
    assert created_clients, "Expected client factory to be invoked"
    assert controllers and controllers[0].setup_calls == 1
    assert controllers[0].pause_calls == [False]
    assert hass.config_entries.forwarded
    forwarded_entry, platforms = hass.config_entries.forwarded[0]
    assert forwarded_entry is entry
    assert list(platforms) == list(integration.PLATFORMS)


@pytest.mark.asyncio
async def test_async_unload_entry_cleans_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unloading an entry removes runtime state."""

    hass = _DummyHass()
    entry = _MockConfigEntry("entry-2", {"endpoint": "https://example.invalid/api"})

    class _DummyExposure:
        def __init__(self, *args: object, **kwargs: object) -> None:  # noqa: ANN001
            self.shutdown_calls = 0

        async def async_setup(self) -> None:  # pragma: no cover - not exercised here
            pass

        async def async_shutdown(self) -> None:
            self.shutdown_calls += 1

        async def async_set_paused(self, paused: bool) -> None:  # noqa: ARG002 - parity with controller
            return None

    exposure_instances: list[_DummyExposure] = []

    def _exposure_factory(*args: object, **kwargs: object) -> _DummyExposure:  # noqa: ANN001
        exposure = _DummyExposure()
        exposure_instances.append(exposure)
        return exposure

    monkeypatch.setattr(integration, "_async_get_clientsession", lambda hass_obj: object())
    monkeypatch.setattr(integration, "AIEmbodiedClient", lambda *args, **kwargs: object())
    monkeypatch.setattr(integration, "ExposureController", _exposure_factory)

    await integration.async_setup(hass, {})
    await integration.async_setup_entry(hass, entry)

    assert entry.entry_id in hass.data[DOMAIN]

    assert await integration.async_unload_entry(hass, entry)
    assert entry.entry_id not in hass.data.get(DOMAIN, {})
    assert exposure_instances and exposure_instances[0].shutdown_calls == 1


@pytest.mark.asyncio
async def test_update_listener_triggers_reload(monkeypatch: pytest.MonkeyPatch) -> None:
    """The update listener requests a reload when invoked."""

    hass = _DummyHass()
    entry = _MockConfigEntry("entry-3", {"endpoint": "https://example.invalid/api"})

    monkeypatch.setattr(integration, "_async_get_clientsession", lambda hass_obj: object())
    monkeypatch.setattr(integration, "AIEmbodiedClient", lambda *args, **kwargs: object())

    await integration.async_setup(hass, {})
    await integration.async_setup_entry(hass, entry)

    assert entry._update_listener is not None

    await entry._update_listener(hass, entry)  # type: ignore[misc]
    assert hass.reload_requests == [entry.entry_id]


@pytest.mark.asyncio
async def test_services_registered_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both helper services are registered with response support."""

    hass = _DummyHass()
    monkeypatch.setattr(integration, "_async_get_clientsession", lambda hass_obj: object())
    monkeypatch.setattr(integration, "AIEmbodiedClient", lambda *args, **kwargs: object())

    class _StubExposure(_BaseExposure):
        pass

    monkeypatch.setattr(
        integration,
        "ExposureController",
        lambda *args, **kwargs: _StubExposure(),
    )

    await integration.async_setup(hass, {})
    entry = _MockConfigEntry("entry-services", {"endpoint": "https://example.invalid/api"})
    await integration.async_setup_entry(hass, entry)

    services = hass.services.registered
    send_meta = services[(DOMAIN, integration.SERVICE_SEND_CONVERSATION_TURN)]
    invoke_meta = services[(DOMAIN, integration.SERVICE_INVOKE_SERVICE)]
    assert send_meta["supports_response"] is True
    assert invoke_meta["supports_response"] is True


@pytest.mark.asyncio
async def test_invoke_service_executes_and_reports(monkeypatch: pytest.MonkeyPatch) -> None:
    """The invoke service helper executes HA services and reports results."""

    hass = _DummyHass()
    entry = _MockConfigEntry("entry-action", {"endpoint": "https://example.invalid/api"})

    clients: list[_RecorderClient] = []

    class _RecorderClient:
        def __init__(self, *args: object, **kwargs: object) -> None:  # noqa: ANN001
            self.calls: list[dict[str, Any]] = []
            clients.append(self)

        async def async_post_json(self, payload: dict[str, Any]) -> None:
            self.calls.append(payload)

    class _StubExposure(_BaseExposure):
        pass

    monkeypatch.setattr(integration, "_async_get_clientsession", lambda hass_obj: object())
    monkeypatch.setattr(integration, "AIEmbodiedClient", _RecorderClient)
    monkeypatch.setattr(integration, "ExposureController", lambda *args, **kwargs: _StubExposure())

    await integration.async_setup(hass, {})
    await integration.async_setup_entry(hass, entry)

    handler = hass.services.registered[(DOMAIN, integration.SERVICE_INVOKE_SERVICE)]["handler"]

    def _call_handler(
        domain: str,
        service: str,
        service_data: dict[str, Any],
        *,
        target: dict[str, Any] | None,
        blocking: bool,
        return_response: bool,
        context: object | None,
    ) -> dict[str, Any]:
        assert domain == "light"
        assert service == "turn_on"
        assert service_data == {"brightness": 255}
        assert blocking is True
        assert return_response is True
        assert target == {"entity_id": "light.kitchen"}
        assert getattr(context, "id", None) == "ctx-1"
        return {"status": "ok"}

    hass.services.async_call_handler = _call_handler

    call_data = {
        "entry_id": entry.entry_id,
        "domain": "light",
        "service": "turn_on",
        "service_data": {"brightness": 255},
        "target": {"entity_id": "light.kitchen"},
        "correlation_id": "corr-1",
        "context_id": "ctx-1",
        "context_user_id": "user-1",
        "context_parent_id": "parent-1",
    }

    response = await handler(type("_Call", (), {"data": call_data})())

    assert response == {
        "success": True,
        "result": {"status": "ok"},
        "correlation_id": "corr-1",
    }

    assert hass.services.calls[0]["domain"] == "light"
    assert hass.bus.events[0][0] == f"{DOMAIN}.action_executed"
    event_payload = hass.bus.events[0][1]
    assert event_payload["success"] is True
    assert event_payload["correlation_id"] == "corr-1"
    assert clients and clients[0].calls
    upstream_payload = clients[0].calls[0]
    assert upstream_payload["type"] == "action_result"
    assert upstream_payload["action"]["result"] == {"status": "ok"}
    assert upstream_payload["action"]["context"]["id"] == "ctx-1"


@pytest.mark.asyncio
async def test_invoke_service_blocked_when_paused(monkeypatch: pytest.MonkeyPatch) -> None:
    """Service invocations are rejected when autonomy is paused."""

    hass = _DummyHass()
    entry = _MockConfigEntry("entry-paused", {"endpoint": "https://example.invalid/api"})
    entry.options[OPTIONS_AUTONOMY_PAUSED] = True

    class _StubExposure:
        async def async_setup(self) -> None:
            return None

        async def async_shutdown(self) -> None:  # pragma: no cover - not invoked here
            return None

        async def async_set_paused(self, paused: bool) -> None:  # noqa: ARG002
            return None

    monkeypatch.setattr(integration, "_async_get_clientsession", lambda hass_obj: object())
    monkeypatch.setattr(integration, "AIEmbodiedClient", lambda *args, **kwargs: object())
    monkeypatch.setattr(integration, "ExposureController", lambda *args, **kwargs: _StubExposure())

    await integration.async_setup(hass, {})
    await integration.async_setup_entry(hass, entry)

    handler = hass.services.registered[(DOMAIN, integration.SERVICE_INVOKE_SERVICE)]["handler"]

    paused_call = type(
        "_Call",
        (),
        {"data": {"entry_id": entry.entry_id, "domain": "light", "service": "turn_on"}},
    )()

    with pytest.raises(HomeAssistantError, match="Autonomy is currently paused"):
        await handler(paused_call)


@pytest.mark.asyncio
async def test_autonomy_state_updates_options(monkeypatch: pytest.MonkeyPatch) -> None:
    """Toggling autonomy updates config entry options and triggers reload."""

    hass = _DummyHass()
    entry = _MockConfigEntry("entry-autonomy", {"endpoint": "https://example.invalid/api"})

    class _StubExposure:
        def __init__(self, *args: object, **kwargs: object) -> None:  # noqa: ANN001
            self.pause_states: list[bool] = []

        async def async_setup(self) -> None:
            return None

        async def async_shutdown(self) -> None:  # pragma: no cover - not invoked here
            return None

        async def async_set_paused(self, paused: bool) -> None:
            self.pause_states.append(paused)

    exposure_instances: list[_StubExposure] = []

    def _exposure_factory(*args: object, **kwargs: object) -> _StubExposure:  # noqa: ANN001
        instance = _StubExposure(*args, **kwargs)
        exposure_instances.append(instance)
        return instance

    monkeypatch.setattr(integration, "_async_get_clientsession", lambda hass_obj: object())
    monkeypatch.setattr(integration, "AIEmbodiedClient", lambda *args, **kwargs: object())
    monkeypatch.setattr(integration, "ExposureController", _exposure_factory)

    await integration.async_setup(hass, {})
    await integration.async_setup_entry(hass, entry)

    runtime = hass.data[DOMAIN][entry.entry_id][DATA_RUNTIME]
    assert runtime.autonomy.paused is False

    await runtime.autonomy.async_set_paused(True)

    assert runtime.autonomy.paused is True
    assert entry.options[OPTIONS_AUTONOMY_PAUSED] is True
    assert hass.reload_requests == [entry.entry_id]
    assert hass.config_entries.updated[-1][OPTIONS_AUTONOMY_PAUSED] is True
    assert exposure_instances and exposure_instances[0].pause_states[-1] is True


@pytest.mark.asyncio
async def test_invoke_service_reports_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    """Service call failures are captured and forwarded."""

    hass = _DummyHass()
    entry = _MockConfigEntry("entry-action-fail", {"endpoint": "https://example.invalid/api"})

    class _RecorderClient:
        def __init__(self, *args: object, **kwargs: object) -> None:  # noqa: ANN001
            self.calls: list[dict[str, Any]] = []

        async def async_post_json(self, payload: dict[str, Any]) -> None:
            self.calls.append(payload)

    monkeypatch.setattr(integration, "_async_get_clientsession", lambda hass_obj: object())
    client = _RecorderClient(None, None)
    monkeypatch.setattr(integration, "AIEmbodiedClient", lambda *args, **kwargs: client)
    monkeypatch.setattr(integration, "ExposureController", lambda *args, **kwargs: _BaseExposure())

    await integration.async_setup(hass, {})
    await integration.async_setup_entry(hass, entry)

    handler = hass.services.registered[(DOMAIN, integration.SERVICE_INVOKE_SERVICE)]["handler"]

    def _call_handler(*args: object, **kwargs: object) -> None:  # noqa: ANN001
        raise HomeAssistantError("boom")

    hass.services.async_call_handler = _call_handler

    call_data = {
        "entry_id": entry.entry_id,
        "domain": "light",
        "service": "turn_on",
    }

    response = await handler(type("_Call", (), {"data": call_data})())
    assert response["success"] is False
    assert "boom" in response["error"]

    event_type, event_payload = hass.bus.events[0]
    assert event_type == f"{DOMAIN}.action_executed"
    assert event_payload["success"] is False
    assert "boom" in event_payload["error"]
    assert client.calls
    assert client.calls[0]["action"]["success"] is False


@pytest.mark.asyncio
async def test_invoke_service_validates_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    """Invalid payload data raises errors before execution."""

    hass = _DummyHass()
    entry = _MockConfigEntry("entry-action-validate", {"endpoint": "https://example.invalid/api"})

    monkeypatch.setattr(integration, "_async_get_clientsession", lambda hass_obj: object())
    monkeypatch.setattr(integration, "AIEmbodiedClient", lambda *args, **kwargs: object())

    class _StubExposure:
        async def async_setup(self) -> None:
            return None

        async def async_shutdown(self) -> None:  # pragma: no cover - unload not exercised
            return None

        async def async_set_paused(self, paused: bool) -> None:  # noqa: ARG002
            return None

    monkeypatch.setattr(
        integration,
        "ExposureController",
        lambda *args, **kwargs: _StubExposure(),
    )

    await integration.async_setup(hass, {})
    await integration.async_setup_entry(hass, entry)

    handler = hass.services.registered[(DOMAIN, integration.SERVICE_INVOKE_SERVICE)]["handler"]

    with pytest.raises(HomeAssistantError):
        await handler(type("_Call", (), {"data": {"entry_id": entry.entry_id, "domain": 123}})())

    with pytest.raises(HomeAssistantError):
        await handler(
            type(
                "_Call",
                (),
                {
                    "data": {
                        "entry_id": entry.entry_id,
                        "domain": "light",
                        "service": "turn_on",
                        "service_data": 5,
                    }
                },
            )(),
        )
