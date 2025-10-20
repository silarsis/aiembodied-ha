"""Core primitives for the Home Assistant stub."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, TypeVar


class HomeAssistant:
    """Very small subset of the Home Assistant core object."""

    def __init__(self) -> None:
        self.data: dict[str, dict[str, Any]] = {}
        self.config_entries: Any = None


@dataclass(slots=True)
class Context:
    """Context information passed with events and conversation inputs."""

    user_id: str | None = None
    id: str | None = None


F = TypeVar("F", bound=Callable[..., Any])


def callback(func: F) -> F:
    """No-op decorator used by the real Home Assistant core."""

    return func


@dataclass(slots=True)
class Event:
    """Simplified event structure delivered to listeners."""

    data: dict[str, Any]
    context: Context | None = None


@dataclass(slots=True)
class State:
    """Minimal state representation mirroring Home Assistant objects."""

    entity_id: str
    state: str
    attributes: dict[str, Any]
    last_changed: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    last_updated: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    @property
    def name(self) -> str | None:
        friendly_name = self.attributes.get("friendly_name")
        if isinstance(friendly_name, str):
            cleaned = friendly_name.strip()
            if cleaned:
                return cleaned
        return None
