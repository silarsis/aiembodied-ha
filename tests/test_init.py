"""Tests for the aiembodied integration setup module."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import pytest

import custom_components.aiembodied as integration
from custom_components.aiembodied.const import DATA_RUNTIME, DOMAIN


@dataclass
class _DummyConfigEntries:
    """Minimal config entries manager for testing reload behavior."""

    async_reload: Callable[[str], Awaitable[None]]


class _DummyHass:
    """Simplified Home Assistant core object for unit tests."""

    def __init__(self) -> None:
        self.data: dict[str, dict[str, object]] = {}
        self.config_entries = _DummyConfigEntries(async_reload=self._async_reload)
        self.reload_requests: list[str] = []
        self.services = _DummyServices()

    async def _async_reload(self, entry_id: str) -> None:
        self.reload_requests.append(entry_id)


class _DummyServices:
    """Service registry stub capturing registrations."""

    def __init__(self) -> None:
        self.registered: dict[tuple[str, str], dict[str, object]] = {}

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


class _MockConfigEntry:
    """Small stub mimicking the ConfigEntry interface used by the integration."""

    def __init__(self, entry_id: str, data: dict[str, object]) -> None:
        self.entry_id = entry_id
        self.data = data
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
        ) -> None:  # noqa: ANN001
            self.hass = hass_obj
            self.client = client_obj
            self.config = config_obj
            self.entry_id = entry_id
            self.setup_calls = 0
            self.shutdown_calls = 0
            controllers.append(self)

        async def async_setup(self) -> None:
            self.setup_calls += 1

        async def async_shutdown(self) -> None:
            self.shutdown_calls += 1

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
