"""Tests for the AI Embodied API client."""

from __future__ import annotations

from typing import Any

import pytest
from aiohttp import ClientError

from custom_components.aiembodied.api_client import (
    AIEmbodiedClient,
    AIEmbodiedClientConfig,
    AIEmbodiedClientError,
)


class _StubResponse:
    """Fake aiohttp response supporting the subset we use."""

    def __init__(self, payload: dict[str, Any], status: int = 200) -> None:
        self._payload = payload
        self.status = status

    async def __aenter__(self) -> "_StubResponse":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:  # type: ignore[override]
        return None

    def raise_for_status(self) -> None:
        if self.status >= 400:
            raise ClientError(f"HTTP {self.status}")

    async def json(self) -> dict[str, Any]:
        return self._payload


class _StubSession:
    """Stubbed session object for testing the client."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self._response: _StubResponse | None = None

    def set_response(self, response: _StubResponse) -> None:
        self._response = response

    def post(
        self,
        url: str,
        *,
        json: dict[str, Any],
        headers: dict[str, str],
        timeout: float,
    ) -> _StubResponse:
        self.requests.append({
            "url": url,
            "json": json,
            "headers": headers,
            "timeout": timeout,
        })
        if not self._response:
            raise AssertionError("Response not configured")
        return self._response


@pytest.mark.asyncio
async def test_async_post_json_merges_headers() -> None:
    """The client merges custom headers and auth token."""

    session = _StubSession()
    session.set_response(_StubResponse({"ok": True}))

    client = AIEmbodiedClient(
        session,  # type: ignore[arg-type]
        AIEmbodiedClientConfig(
            endpoint="https://example.invalid/api",
            auth_token="Bearer token",
            headers={"X-Test": "1"},
            timeout=5,
        ),
    )

    response = await client.async_post_json({"foo": "bar"})
    assert response == {"ok": True}

    request = session.requests.pop()
    assert request["url"] == "https://example.invalid/api"
    assert request["json"] == {"foo": "bar"}
    assert request["timeout"] == 5
    assert request["headers"] == {
        "Content-Type": "application/json",
        "X-Test": "1",
        "Authorization": "Bearer token",
    }


@pytest.mark.asyncio
async def test_async_post_json_raises_client_error() -> None:
    """Client errors are wrapped in AIEmbodiedClientError."""

    class _FailingSession(_StubSession):
        def post(self, *args, **kwargs):  # noqa: ANN001
            raise ClientError("boom")

    session = _FailingSession()
    client = AIEmbodiedClient(
        session,  # type: ignore[arg-type]
        AIEmbodiedClientConfig(endpoint="https://example.invalid/api"),
    )

    with pytest.raises(AIEmbodiedClientError):
        await client.async_post_json({})
