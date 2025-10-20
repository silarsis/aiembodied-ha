"""Event helpers for the Home Assistant stub."""

from __future__ import annotations

from typing import Any, Callable, Iterable

MATCH_ALL: object = object()


def async_track_state_change_event(
    hass: Any, entity_ids: Iterable[str] | object, action: Callable[[Any], Any]
) -> Callable[[], None]:
    """Register a state change listener (no-op in the stub)."""

    return lambda: None
