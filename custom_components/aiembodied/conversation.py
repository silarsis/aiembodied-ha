"""Conversation agent implementation for the aiembodied integration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Mapping

from homeassistant.components import conversation

from .api_client import AIEmbodiedClient, AIEmbodiedClientError

if TYPE_CHECKING:  # pragma: no cover - imports for type checking only
    from . import IntegrationConfig


class AIEmbodiedConversationAgent(conversation.AbstractConversationAgent):
    """Home Assistant conversation agent backed by the Embodied AI service."""

    def __init__(self, client: AIEmbodiedClient, config: "IntegrationConfig") -> None:
        self._client = client
        self._config = config

    @property
    def supported_languages(self) -> set[str]:
        """Return the set of supported languages."""

        return {"*"}

    @property
    def attribution(self) -> dict[str, str]:
        """Return attribution metadata for the agent."""

        return {"name": "Embodied AI"}

    async def async_handle(
        self, user_input: conversation.ConversationInput
    ) -> conversation.ConversationResult:
        """Process the conversation input and return a result."""

        payload = self._build_payload(user_input)

        try:
            response = await self._client.async_post_json(payload)
        except AIEmbodiedClientError as exc:
            raise conversation.ConversationError(
                "Error communicating with the Embodied AI service"
            ) from exc

        text = self._extract_response_text(response)
        if text is None:
            raise conversation.ConversationError(
                "Embodied AI service did not return a textual response"
            )

        conversation_id = self._extract_conversation_id(user_input, response)
        return conversation.ConversationResult(
            response=conversation.ConversationResponse(
                text=text,
                language=user_input.language,
                data=response,
            ),
            conversation_id=conversation_id,
        )

    def _build_payload(self, user_input: conversation.ConversationInput) -> Mapping[str, Any]:
        """Construct the payload sent to the upstream service."""

        config_block: dict[str, Any] = {
            "exposure": list(self._config.exposure),
            "batching": self._config.batching,
        }
        if self._config.throttle is not None:
            config_block["throttle"] = self._config.throttle
        if self._config.routing:
            config_block["routing"] = dict(self._config.routing)

        payload: dict[str, Any] = {
            "input": {
                "text": user_input.text,
                "conversation_id": user_input.conversation_id,
                "language": user_input.language,
                "device_id": user_input.device_id,
            },
            "config": config_block,
        }

        context = self._serialize_context(user_input.context)
        if context is not None:
            payload["context"] = context

        return payload

    def _serialize_context(self, context: Any) -> dict[str, Any] | None:
        """Serialize Home Assistant context objects into dictionaries."""

        if context is None:
            return None

        result: dict[str, Any] = {}
        for attr in ("id", "user_id", "parent_id"):
            value = getattr(context, attr, None)
            if value is not None:
                result[attr] = value
        return result or None

    def _extract_response_text(self, response: Mapping[str, Any]) -> str | None:
        """Normalize textual content from the upstream response."""

        candidates: list[Any] = [
            response.get("reply"),
            response.get("text"),
            response.get("response"),
            response.get("message"),
        ]
        for candidate in candidates:
            text = self._coerce_text(candidate)
            if text:
                return text
        return None

    def _extract_conversation_id(
        self,
        user_input: conversation.ConversationInput,
        response: Mapping[str, Any],
    ) -> str | None:
        """Determine the conversation id to report back to Home Assistant."""

        candidate = response.get("conversation_id")
        if isinstance(candidate, str) and candidate:
            return candidate
        return user_input.conversation_id

    @staticmethod
    def _coerce_text(candidate: Any) -> str | None:
        """Coerce potential response representations into a string."""

        if isinstance(candidate, str):
            return candidate.strip() or None
        if isinstance(candidate, Mapping):
            inner = candidate.get("text")
            if isinstance(inner, str):
                return inner.strip() or None
        return None
