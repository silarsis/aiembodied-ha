"""Core primitives for the Home Assistant stub."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


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
