"""Minimal subset of the voluptuous API for unit testing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping


@dataclass(frozen=True)
class _Marker:
    """Represents a required or optional key in a schema."""

    key: str
    default: Any | None = None
    required: bool = False

    def __hash__(self) -> int:
        return hash((self.key, self.default, self.required))


def Required(key: str, default: Any | None = None) -> _Marker:
    """Mark a schema key as required."""

    return _Marker(key, default, True)


def Optional(key: str, default: Any | None = None) -> _Marker:
    """Mark a schema key as optional."""

    return _Marker(key, default, False)


class Schema(dict):
    """Very small schema implementation for form building."""

    def __init__(self, schema: Mapping[Any, Callable[..., Any] | type]) -> None:
        super().__init__(schema)

    def __call__(self, data: Mapping[str, Any]) -> Mapping[str, Any]:
        return data
