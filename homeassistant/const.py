"""Constant definitions for the Home Assistant stub."""

from __future__ import annotations

from enum import StrEnum

CONF_HEADERS = "headers"


class Platform(StrEnum):
    """Minimal platform identifiers used in tests."""

    BINARY_SENSOR = "binary_sensor"
    SENSOR = "sensor"
    SWITCH = "switch"
