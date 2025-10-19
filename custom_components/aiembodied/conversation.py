"""Conversation agent bridging Assist with the AI Embodied runtime."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Mapping, Sequence

from homeassistant.components.conversation.agent_manager import (
    ConversationTraceEvent,
    ConversationTraceEventType,
    async_conversation_trace,
)
from homeassistant.components.conversation.models import (
    AbstractConversationAgent,
    ConversationInput,
    ConversationResult,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import intent

from .api_client import AIEmbodiedClient, AIEmbodiedClientError

if TYPE_CHECKING:
    from .__init__ import IntegrationConfig

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class AIEmbodiedConversationAgent(AbstractConversationAgent):
    """Home Assistant conversation agent backed by the AI Embodied service."""

    hass: HomeAssistant
    entry_id: str
    client: AIEmbodiedClient
    config: "IntegrationConfig"
    options: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Normalize mutable runtime inputs."""
        self.options = dict(self.options)

    @property
    def supported_languages(self) -> list[str] | Literal["*"]:
        """Expose that the upstream service accepts any language."""

        return ["*"]

    async def async_process(self, user_input: ConversationInput) -> ConversationResult:
        """Forward the conversation turn to the upstream AI runtime."""

        request_payload = self._build_request_payload(user_input)
        with async_conversation_trace() as trace:
            trace.add_event(
                ConversationTraceEvent(
                    ConversationTraceEventType.AGENT_DETAIL,
                    {
                        "entry_id": self.entry_id,
                        "endpoint": self.config.endpoint,
                        "exposure": list(self.config.exposure),
                        "routing": dict(self.config.routing),
                    },
                )
            )
            trace.add_event(
                ConversationTraceEvent(
                    ConversationTraceEventType.TOOL_CALL,
                    {
                        "tool": "aiembodied.async_post_json",
                        "input": request_payload,
                    },
                )
            )
            try:
                response_payload = await self.client.async_post_json(request_payload)
            except AIEmbodiedClientError as err:
                trace.add_event(
                    ConversationTraceEvent(
                        ConversationTraceEventType.ASYNC_PROCESS,
                        {"error": str(err)},
                    )
                )
                _LOGGER.warning(
                    "AI Embodied conversation request failed for %s: %s",
                    self.entry_id,
                    err,
                )
                error_response = _build_error_intent_response(
                    user_input.language, str(err)
                )
                return ConversationResult(
                    response=error_response,
                    conversation_id=user_input.conversation_id,
                )

            trace.add_event(
                ConversationTraceEvent(
                    ConversationTraceEventType.ASYNC_PROCESS,
                    {"response": response_payload},
                )
            )

        conversation_id, intent_response = self._translate_response(
            user_input, response_payload
        )
        return ConversationResult(
            response=intent_response,
            conversation_id=conversation_id,
        )

    def _build_request_payload(self, user_input: ConversationInput) -> dict[str, Any]:
        """Create the JSON payload sent to the AI runtime."""

        context = dict(user_input.context.as_dict())
        payload: dict[str, Any] = {
            "entry_id": self.entry_id,
            "endpoint": self.config.endpoint,
            "conversation": {
                "text": user_input.text,
                "conversation_id": user_input.conversation_id,
                "device_id": user_input.device_id,
                "language": user_input.language,
                "agent_id": user_input.agent_id,
                "context": context,
            },
            "config": {
                "exposure": list(self.config.exposure),
                "throttle": self.config.throttle,
                "batching": self.config.batching,
                "routing": dict(self.config.routing),
            },
        }
        if self.options:
            payload["options"] = dict(self.options)
        return payload

    def _translate_response(
        self,
        user_input: ConversationInput,
        payload: Mapping[str, Any] | None,
    ) -> tuple[str | None, intent.IntentResponse]:
        """Convert the upstream JSON response into a Home Assistant response."""

        payload = _ensure_mapping(payload) or {}
        conversation_id = _coerce_str(payload.get("conversation_id"))
        if conversation_id is None:
            conversation_id = user_input.conversation_id

        response_section = _ensure_mapping(payload.get("response"))
        error_section = _ensure_mapping(payload.get("error"))
        if not error_section and response_section:
            error_section = _ensure_mapping(response_section.get("error"))

        response = intent.IntentResponse(language=user_input.language)

        if error_section:
            _apply_error(response, error_section)
            return conversation_id, response

        if response_section:
            _apply_response_metadata(response, response_section)
        else:
            response.async_set_speech(
                "I didn't receive a usable reply from the AI Embodied service."
            )
        return conversation_id, response


def _build_error_intent_response(
    language: str, message: str
) -> intent.IntentResponse:
    """Return a standardized error response when the API call fails."""

    response = intent.IntentResponse(language=language)
    _apply_error(
        response,
        {
            "code": intent.IntentResponseErrorCode.UNKNOWN.value,
            "message": message,
        },
    )
    return response


def _apply_error(
    response: intent.IntentResponse, error: Mapping[str, Any]
) -> None:
    """Apply an error payload to an intent response."""

    code_value = str(
        error.get("code", intent.IntentResponseErrorCode.UNKNOWN.value)
    )
    try:
        code = intent.IntentResponseErrorCode(code_value)
    except ValueError:
        code = intent.IntentResponseErrorCode.UNKNOWN
    message = str(
        error.get("message")
        or "The AI Embodied service could not process this request."
    )
    response.async_set_error(code, message)


def _apply_response_metadata(
    response: intent.IntentResponse, payload: Mapping[str, Any]
) -> None:
    """Populate an intent response using the upstream payload."""

    speech = payload.get("speech")
    if speech is not None:
        _apply_speech(response, speech)

    reprompt = payload.get("reprompt")
    if reprompt is not None:
        _apply_reprompt(response, reprompt)

    card = payload.get("card")
    if card is not None:
        _apply_card(response, card)

    speech_slots = _ensure_mapping(payload.get("speech_slots"))
    if speech_slots:
        response.async_set_speech_slots(dict(speech_slots))

    response_type = payload.get("response_type")
    if isinstance(response_type, str):
        try:
            response.response_type = intent.IntentResponseType(response_type)
        except ValueError:
            pass

    data = _ensure_mapping(payload.get("data"))
    if data:
        targets = _decode_targets(data.get("targets"))
        if targets:
            response.async_set_targets(targets)

        success = _decode_targets(data.get("success"))
        failed = _decode_targets(data.get("failed"))
        if success or failed:
            response.async_set_results(success, failed)


def _apply_speech(response: intent.IntentResponse, data: Any) -> None:
    """Apply speech blocks from the upstream payload."""

    if isinstance(data, str):
        if data:
            response.async_set_speech(data)
        return

    mapping = _ensure_mapping(data)
    if not mapping:
        return

    for speech_type, details in mapping.items():
        text, extra = _extract_speech_fields(details, speech_key="speech")
        if text:
            response.async_set_speech(
                text,
                speech_type=str(speech_type),
                extra_data=extra,
            )


def _apply_reprompt(response: intent.IntentResponse, data: Any) -> None:
    """Apply reprompt blocks from the upstream payload."""

    if isinstance(data, str):
        if data:
            response.async_set_reprompt(data)
        return

    mapping = _ensure_mapping(data)
    if not mapping:
        return

    for prompt_type, details in mapping.items():
        text, extra = _extract_speech_fields(details, speech_key="reprompt")
        if text:
            response.async_set_reprompt(
                text,
                speech_type=str(prompt_type),
                extra_data=extra,
            )


def _apply_card(response: intent.IntentResponse, data: Any) -> None:
    """Populate card metadata on the intent response."""

    mapping = _ensure_mapping(data)
    if not mapping:
        return

    for card_type, details in mapping.items():
        details_mapping = _ensure_mapping(details)
        if not details_mapping:
            continue
        title = str(details_mapping.get("title", ""))
        content = str(details_mapping.get("content", ""))
        if not title and not content:
            continue
        response.async_set_card(title, content, card_type=str(card_type))


def _decode_targets(raw: Any) -> list[intent.IntentResponseTarget]:
    """Convert serialized target data into IntentResponseTarget objects."""

    if not isinstance(raw, Sequence):
        return []

    targets: list[intent.IntentResponseTarget] = []
    for item in raw:
        mapping = _ensure_mapping(item)
        if not mapping:
            continue
        name = str(mapping.get("name", "")).strip()
        if not name:
            continue
        type_value = str(
            mapping.get(
                "type",
                intent.IntentResponseTargetType.CUSTOM.value,
            )
        )
        try:
            target_type = intent.IntentResponseTargetType(type_value)
        except ValueError:
            target_type = intent.IntentResponseTargetType.CUSTOM
        identifier = mapping.get("id")
        targets.append(
            intent.IntentResponseTarget(
                name=name,
                type=target_type,
                id=str(identifier) if identifier is not None else None,
            )
        )
    return targets


def _extract_speech_fields(
    data: Any, *, speech_key: str
) -> tuple[str, Any | None]:
    """Pull the speech text and optional extra data from a payload block."""

    mapping = _ensure_mapping(data)
    if not mapping:
        text = str(data or "").strip()
        return text, None

    text = str(
        mapping.get(speech_key)
        or mapping.get("speech")
        or mapping.get("text")
        or ""
    ).strip()
    extra = mapping.get("extra_data")
    return text, extra


def _ensure_mapping(value: Any) -> Mapping[str, Any] | None:
    """Return the value if it is a mapping."""

    if isinstance(value, Mapping):
        return value
    return None


def _coerce_str(value: Any) -> str | None:
    """Convert a value to a string when possible."""

    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return str(value)
