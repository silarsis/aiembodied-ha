"""Async client for communicating with the upstream AI endpoint."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from aiohttp import ClientError, ClientSession

from .const import DEFAULT_TIMEOUT


@dataclass(slots=True)
class AIEmbodiedClientConfig:
    """Configuration options for the AI Embodied client."""

    endpoint: str
    auth_token: str | None = None
    headers: Mapping[str, str] | None = None
    timeout: float = DEFAULT_TIMEOUT


class AIEmbodiedClient:
    """Client responsible for interacting with the AI service."""

    def __init__(self, session: ClientSession, config: AIEmbodiedClientConfig) -> None:
        self._session = session
        self._config = config

    @property
    def config(self) -> AIEmbodiedClientConfig:
        """Return the current configuration."""

        return self._config

    async def async_post_json(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Send a JSON payload to the AI endpoint and return the decoded response.

        This method does not yet implement retries or advanced error handling. Future
        implementation steps will extend it with additional safeguards as described in
        the PRD.
        """

        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._config.headers:
            headers.update(self._config.headers)
        if self._config.auth_token:
            headers.setdefault("Authorization", self._config.auth_token)

        try:
            async with self._session.post(
                self._config.endpoint,
                json=payload,
                headers=headers,
                timeout=self._config.timeout,
            ) as response:
                response.raise_for_status()
                return await response.json()
        except ClientError as exc:
            raise AIEmbodiedClientError("Error communicating with AI endpoint") from exc


class AIEmbodiedClientError(RuntimeError):
    """Raised when the AI Embodied client encounters an error."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
