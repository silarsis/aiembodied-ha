"""Unit tests for the autonomy controller."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Mapping

import pytest

from custom_components.aiembodied.autonomy import AutonomyController
from custom_components.aiembodied.const import OPTIONS_AUTONOMY_PAUSED


class _StubServices:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, Mapping[str, Any], bool]] = []

    async def async_call(
        self,
        domain: str,
        service: str,
        data: Mapping[str, Any],
        *,
        blocking: bool = False,
    ) -> None:
        self.calls.append((domain, service, dict(data), blocking))


class _StubConfigEntries:
    def __init__(self) -> None:
        self.updated: list[Mapping[str, Any]] = []

    async def async_update_entry(
        self,
        entry: "_StubConfigEntry",
        *,
        data: Mapping[str, Any] | None = None,
        options: Mapping[str, Any] | None = None,
    ) -> None:
        if options is not None:
            entry.options = dict(options)
            self.updated.append(entry.options)
        if entry.update_listener is not None:
            await entry.update_listener(None, entry)


class _StubConfigEntry:
    def __init__(self, entry_id: str) -> None:
        self.entry_id = entry_id
        self.options: dict[str, Any] = {}
        self.update_listener: Callable[[Any, Any], Awaitable[None]] | None = None

    def add_update_listener(
        self, listener: Callable[[Any, Any], Awaitable[None]]
    ) -> Callable[[], None]:
        self.update_listener = listener
        return lambda: None


class _StubHass:
    def __init__(self) -> None:
        self.services = _StubServices()
        self.config_entries = _StubConfigEntries()
        self.reloads: list[str] = []

    async def request_reload(self, entry_id: str) -> None:
        self.reloads.append(entry_id)


@pytest.mark.asyncio
async def test_async_set_paused_updates_options_and_callbacks() -> None:
    """Setting the pause state persists options and notifies listeners."""

    hass = _StubHass()
    entry = _StubConfigEntry("entry-1")

    controller = AutonomyController(hass, entry)
    callback_states: list[bool] = []

    async def _update_listener(hass_obj: Any, config_entry: Any) -> None:  # noqa: ANN001
        await hass.request_reload(config_entry.entry_id)

    entry.add_update_listener(_update_listener)

    async def _pause_listener(paused: bool) -> None:
        callback_states.append(paused)

    controller.add_pause_callbacks([_pause_listener])

    updates = 0

    def _listener() -> None:
        nonlocal updates
        updates += 1

    controller.async_add_listener(_listener)

    await controller.async_set_paused(True)

    assert controller.paused is True
    assert callback_states == [True]
    assert hass.reloads == [entry.entry_id]
    assert hass.config_entries.updated[-1][OPTIONS_AUTONOMY_PAUSED] is True
    assert updates == 1


@pytest.mark.asyncio
async def test_record_failure_triggers_notification() -> None:
    """Repeated failures raise a persistent notification."""

    hass = _StubHass()
    entry = _StubConfigEntry("entry-2")
    controller = AutonomyController(hass, entry, failure_threshold=2)

    await controller.record_failure("event", "first")
    assert hass.services.calls == []
    assert controller.diagnostics.consecutive_failures == 1

    await controller.record_failure("event", "second")
    assert hass.services.calls
    domain, service, data, blocking = hass.services.calls[0]
    assert domain == "persistent_notification"
    assert service == "create"
    assert data["notification_id"].endswith(entry.entry_id)
    assert blocking is False
    assert controller.upstream_available is False

    controller.record_success()
    assert controller.upstream_available is True
    assert controller.diagnostics.consecutive_failures == 0
