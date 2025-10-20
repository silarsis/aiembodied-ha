"""Unit tests for the exposure controller."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Coroutine

import pytest

from custom_components.aiembodied import IntegrationConfig
from custom_components.aiembodied.api_client import AIEmbodiedClientError
from custom_components.aiembodied.exposure import ExposureController
from homeassistant.core import Context, State


@dataclass
class _FakeEvent:
    """Lightweight representation of a Home Assistant event."""

    entity_id: str
    old_state: State | None
    new_state: State | None
    context: Context | None

    def __post_init__(self) -> None:
        self.data = {
            "entity_id": self.entity_id,
            "old_state": self.old_state,
            "new_state": self.new_state,
        }


class _DummyBus:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    def async_fire(self, event_type: str, event_data: dict[str, object]) -> None:
        self.events.append((event_type, event_data))


class _DummyHass:
    def __init__(self) -> None:
        self.loop = asyncio.get_running_loop()
        self.bus = _DummyBus()
        self._tasks: list[asyncio.Task[None]] = []

    def async_create_task(
        self, coro: Coroutine[Any, Any, Any] | asyncio.Future
    ) -> asyncio.Task:
        task = self.loop.create_task(coro)
        self._tasks.append(task)
        return task

    async def async_drain(self) -> None:
        for task in list(self._tasks):
            await task
        self._tasks = [task for task in self._tasks if not task.done()]


@pytest.mark.asyncio
async def test_controller_forwards_matching_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """State changes for exposed entities are forwarded to the client."""

    hass = _DummyHass()
    config = IntegrationConfig(
        endpoint="https://example.invalid/api",
        auth_token=None,
        headers={},
        exposure=["light.kitchen"],
    )

    callbacks: list[object] = []

    def _fake_track_state_change_event(hass_obj, entity_ids, action):  # noqa: ANN001
        assert hass_obj is hass
        callbacks.append(action)
        return lambda: None

    monkeypatch.setattr(
        "custom_components.aiembodied.exposure.async_track_state_change_event",
        _fake_track_state_change_event,
    )

    client_calls: list[dict[str, object]] = []

    class _DummyClient:
        async def async_post_json(self, payload: dict[str, object]) -> None:
            client_calls.append(payload)

    controller = ExposureController(hass, _DummyClient(), config, entry_id="entry-1")
    await controller.async_setup()
    assert callbacks, "Expected state listener to be registered"

    event = _FakeEvent(
        entity_id="light.kitchen",
        old_state=State("light.kitchen", "off", {"friendly_name": "Kitchen Light"}),
        new_state=State("light.kitchen", "on", {"friendly_name": "Kitchen Light"}),
        context=Context(id="ctx-1", user_id="user-1"),
    )

    controller._handle_state_change(event)
    await hass.async_drain()

    assert client_calls and client_calls[0]["event"] == "state_changed"
    data = client_calls[0]["data"]
    assert data["entity_id"] == "light.kitchen"
    assert data["context"]["id"] == "ctx-1"
    assert hass.bus.events[0][0] == "aiembodied.update_forwarded"
    assert hass.bus.events[0][1]["success"] is True


@pytest.mark.asyncio
async def test_controller_skips_unexposed_entities(monkeypatch: pytest.MonkeyPatch) -> None:
    """Entities outside the exposure list are ignored."""

    hass = _DummyHass()
    config = IntegrationConfig(
        endpoint="https://example.invalid/api",
        auth_token=None,
        headers={},
        exposure=["sensor.*"],
    )

    monkeypatch.setattr(
        "custom_components.aiembodied.exposure.async_track_state_change_event",
        lambda *args, **kwargs: (lambda: None),
    )

    class _DummyClient:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        async def async_post_json(self, payload: dict[str, object]) -> None:
            self.calls.append(payload)

    client = _DummyClient()
    controller = ExposureController(hass, client, config, entry_id="entry-2")
    await controller.async_setup()

    event = _FakeEvent(
        entity_id="light.kitchen",
        old_state=State("light.kitchen", "off", {}),
        new_state=State("light.kitchen", "on", {}),
        context=None,
    )

    controller._handle_state_change(event)
    await hass.async_drain()

    assert not client.calls
    assert not hass.bus.events


@pytest.mark.asyncio
async def test_controller_records_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    """Errors raised by the client are captured in the audit event."""

    hass = _DummyHass()
    config = IntegrationConfig(
        endpoint="https://example.invalid/api",
        auth_token=None,
        headers={},
        exposure=["light.*"],
    )

    monkeypatch.setattr(
        "custom_components.aiembodied.exposure.async_track_state_change_event",
        lambda *args, **kwargs: (lambda: None),
    )

    class _FailingClient:
        async def async_post_json(self, payload: dict[str, object]) -> None:
            raise AIEmbodiedClientError("failure")

    controller = ExposureController(hass, _FailingClient(), config, entry_id="entry-3")
    await controller.async_setup()

    controller._handle_state_change(
        _FakeEvent(
            entity_id="light.desk",
            old_state=State("light.desk", "off", {}),
            new_state=State("light.desk", "on", {}),
            context=None,
        )
    )
    await hass.async_drain()

    assert hass.bus.events
    event_type, data = hass.bus.events[0]
    assert event_type == "aiembodied.update_forwarded"
    assert data["success"] is False
    assert "failure" in data["error"]
