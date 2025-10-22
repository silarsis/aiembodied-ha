"""Autonomy controls and diagnostics for the aiembodied integration."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable, Iterable

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback

from .const import (
    AUTONOMY_FAILURE_THRESHOLD,
    NOTIFICATION_AUTONOMY_FAILURE,
    OPTIONS_AUTONOMY_PAUSED,
)

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class AutonomyDiagnostics:
    """Diagnostic information surfaced through entities."""

    consecutive_failures: int = 0
    last_failure: str | None = None
    last_source: str | None = None


class AutonomyController:
    """Track autonomy state, diagnostics, and persistence."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        *,
        initial_paused: bool = False,
        failure_threshold: int = AUTONOMY_FAILURE_THRESHOLD,
    ) -> None:
        self._hass = hass
        self._entry = entry
        self._paused = initial_paused
        self._failure_threshold = max(1, failure_threshold)
        self._diagnostics = AutonomyDiagnostics()
        self._listeners: list[Callable[[], None]] = []
        self._pause_callbacks: list[
            Callable[[bool], Awaitable[None] | None]
        ] = []
        self._notification_active = False

    @property
    def entry_id(self) -> str:
        """Return the associated config entry id."""

        return self._entry.entry_id

    @property
    def paused(self) -> bool:
        """Return whether automation is currently paused."""

        return self._paused

    @property
    def diagnostics(self) -> AutonomyDiagnostics:
        """Return diagnostic information for observers."""

        return self._diagnostics

    @property
    def upstream_available(self) -> bool:
        """Return True when the upstream service is considered healthy."""

        return self._diagnostics.consecutive_failures == 0

    def async_add_listener(self, callback: Callable[[], None]) -> Callable[[], None]:
        """Register a listener invoked when state changes."""

        self._listeners.append(callback)

        def _remove() -> None:
            try:
                self._listeners.remove(callback)
            except ValueError:  # pragma: no cover - defensive cleanup
                pass

        return _remove

    def add_pause_callbacks(
        self, callbacks: Iterable[Callable[[bool], Awaitable[None] | None]]
    ) -> None:
        """Register callbacks notified when the pause state updates."""

        self._pause_callbacks.extend(callbacks)

    async def async_set_paused(
        self,
        paused: bool,
        *,
        persist: bool = True,
        force_notify: bool = False,
    ) -> None:
        """Update the pause state and notify observers."""

        state_changed = paused != self._paused
        self._paused = paused
        if persist:
            await self._async_update_entry_options(paused)
        if state_changed or force_notify:
            await self._async_apply_pause_state(paused)
            self._async_notify_listeners()

    async def _async_apply_pause_state(self, paused: bool) -> None:
        for pause_callback in list(self._pause_callbacks):
            try:
                result = pause_callback(paused)
            except Exception:  # pragma: no cover - defensive logging
                _LOGGER.exception("Pause callback failed", exc_info=True)
                continue
            if asyncio.iscoroutine(result):
                await result

    @callback
    def record_success(self) -> None:
        """Reset diagnostics on a successful upstream interaction."""

        if self._diagnostics.consecutive_failures == 0 and not self._notification_active:
            return
        self._diagnostics.consecutive_failures = 0
        self._diagnostics.last_failure = None
        self._diagnostics.last_source = None
        self._notification_active = False
        self._async_notify_listeners()

    async def record_failure(self, source: str, message: str) -> None:
        """Increment failure counters and raise notifications as needed."""

        self._diagnostics.consecutive_failures += 1
        self._diagnostics.last_source = source
        self._diagnostics.last_failure = message
        self._async_notify_listeners()

        if self._diagnostics.consecutive_failures < self._failure_threshold:
            return
        if self._notification_active:
            return
        self._notification_active = True
        await self._hass.services.async_call(
            "persistent_notification",
            "create",
            {
                "title": "Embodied AI connectivity issues",
                "message": (
                    "The Embodied AI integration has encountered multiple "
                    "communication failures. Autonomy may be degraded."
                ),
                "notification_id": f"{NOTIFICATION_AUTONOMY_FAILURE}_{self.entry_id}",
            },
            blocking=False,
        )

    def _async_notify_listeners(self) -> None:
        for listener in list(self._listeners):
            try:
                listener()
            except Exception:  # pragma: no cover - defensive logging
                _LOGGER.exception("Autonomy listener failed", exc_info=True)

    async def _async_update_entry_options(self, paused: bool) -> None:
        """Persist the autonomy flag into the config entry options."""

        options = dict(self._entry.options)
        if options.get(OPTIONS_AUTONOMY_PAUSED) == paused:
            return
        options[OPTIONS_AUTONOMY_PAUSED] = paused
        await self._hass.config_entries.async_update_entry(
            self._entry,
            options=options,
        )
