"""Conversation component stubs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass(slots=True)
class ConversationInput:
    """User input provided to a conversation agent."""

    text: str
    conversation_id: str | None = None
    language: str | None = None
    device_id: str | None = None
    context: Any | None = None


@dataclass(slots=True)
class ConversationResponse:
    """Response payload returned by an agent."""

    text: str | None = None
    language: str | None = None
    data: dict[str, Any] | None = None


@dataclass(slots=True)
class ConversationResult:
    """Result wrapper for conversation responses."""

    response: ConversationResponse
    conversation_id: str | None = None


class ConversationError(RuntimeError):
    """Base error raised during agent processing."""


@runtime_checkable
class AbstractConversationAgent(Protocol):
    """Interface for conversation agents."""

    @property
    def supported_languages(self) -> set[str]:  # pragma: no cover - protocol definition
        """Languages supported by the agent."""

    @property
    def attribution(self) -> dict[str, str] | None:  # pragma: no cover - protocol definition
        """Attribution metadata for responses."""

    async def async_handle(self, user_input: ConversationInput) -> ConversationResult:
        """Process the user input."""


_AGENTS: dict[str, AbstractConversationAgent] = {}


def async_set_agent(
    hass: Any,
    entry: Any,
    agent: AbstractConversationAgent | None,
) -> None:
    """Register or remove a conversation agent for a config entry."""

    entry_id = getattr(entry, "entry_id", str(entry))
    if agent is None:
        _AGENTS.pop(entry_id, None)
        return
    _AGENTS[entry_id] = agent


def async_get_agent(hass: Any, entry_id: str) -> AbstractConversationAgent | None:
    """Return the agent registered for the entry id if present."""

    return _AGENTS.get(entry_id)


def async_unset_agent(hass: Any, entry: Any) -> None:
    """Convenience wrapper to remove an agent."""

    async_set_agent(hass, entry, None)
