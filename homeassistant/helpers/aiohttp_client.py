"""aiohttp client helpers stub."""

from __future__ import annotations

from aiohttp import ClientSession


def async_get_clientsession(hass: object) -> ClientSession:  # noqa: ANN001 - signature compatibility
    """Return a new client session for tests."""

    return ClientSession()
