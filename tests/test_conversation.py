"""Unit tests for the AI Embodied conversation agent."""

from __future__ import annotations

import pytest
from homeassistant.components.conversation.models import Context, ConversationInput
from homeassistant.helpers.intent import (
    IntentResponseErrorCode,
    IntentResponseType,
)

from custom_components.aiembodied.__init__ import IntegrationConfig
from custom_components.aiembodied.api_client import AIEmbodiedClientError
from custom_components.aiembodied.conversation import AIEmbodiedConversationAgent


class _StubHass:
    """Minimal stub for the Home Assistant instance."""

    pass


class _RecordingClient:
    """Fake API client that records payloads and returns a canned response."""

    def __init__(self, response: dict[str, object]) -> None:
        self._response = response
        self.requests: list[dict[str, object]] = []

    async def async_post_json(self, payload: dict[str, object]) -> dict[str, object]:
        self.requests.append(payload)
        return self._response


@pytest.mark.asyncio
async def test_async_process_successful_round_trip() -> None:
    """A successful upstream response is translated into an intent response."""

    hass = _StubHass()
    config = IntegrationConfig(
        endpoint="https://example.invalid/api",
        auth_token="token-123",
        headers={"X-Test": "1"},
        exposure=["light.kitchen"],
        throttle=15,
        batching=True,
        routing={"en-US": "assist-pipeline"},
    )

    response_payload = {
        "conversation_id": "server-conv",
        "response": {
            "speech": {
                "plain": {
                    "speech": "Greetings!",
                    "extra_data": {"foo": "bar"},
                }
            },
            "reprompt": {"plain": {"speech": "Anything else?"}},
            "card": {
                "simple": {"title": "Card Title", "content": "Card Content"}
            },
            "speech_slots": {"location": "Kitchen"},
            "response_type": "query_answer",
            "data": {
                "targets": [
                    {
                        "name": "Kitchen Light",
                        "type": "entity",
                        "id": "light.kitchen",
                    }
                ],
                "success": [
                    {
                        "name": "Kitchen Light",
                        "type": "entity",
                        "id": "light.kitchen",
                    }
                ],
                "failed": [],
            },
        },
    }

    client = _RecordingClient(response_payload)
    agent = AIEmbodiedConversationAgent(
        hass=hass,
        entry_id="entry-1",
        client=client,
        config=config,
        options={"debug": True},
    )

    context = Context(user_id="user-1", parent_id="parent-1", id="ctx-1")
    user_input = ConversationInput(
        text="Hello there",
        context=context,
        conversation_id="conv-1",
        device_id="device-1",
        language="en-US",
        agent_id="assist-agent",
    )

    result = await agent.async_process(user_input)

    assert client.requests, "Expected the client to receive a payload"
    request_payload = client.requests[0]
    assert request_payload["conversation"]["text"] == "Hello there"
    assert request_payload["conversation"]["context"]["id"] == "ctx-1"
    assert request_payload["config"]["routing"] == {"en-US": "assist-pipeline"}
    assert request_payload["options"] == {"debug": True}

    assert result.conversation_id == "server-conv"
    response = result.response
    assert response.response_type == IntentResponseType.QUERY_ANSWER
    assert response.speech["plain"]["speech"] == "Greetings!"
    assert response.speech["plain"]["extra_data"] == {"foo": "bar"}
    assert response.reprompt["plain"]["reprompt"] == "Anything else?"
    assert response.card["simple"]["title"] == "Card Title"
    assert response.card["simple"]["content"] == "Card Content"
    assert response.speech_slots == {"location": "Kitchen"}
    assert response.intent_targets[0].name == "Kitchen Light"
    assert response.success_results[0].id == "light.kitchen"


@pytest.mark.asyncio
async def test_async_process_handles_client_error() -> None:
    """Client errors are converted into error intent responses."""

    hass = _StubHass()
    config = IntegrationConfig(
        endpoint="https://example.invalid/api",
        auth_token=None,
        headers={},
        exposure=[],
        throttle=None,
        batching=False,
        routing={},
    )

    class _FailingClient:
        async def async_post_json(self, payload: dict[str, object]) -> dict[str, object]:
            raise AIEmbodiedClientError("Upstream failure")

    agent = AIEmbodiedConversationAgent(
        hass=hass,
        entry_id="entry-2",
        client=_FailingClient(),
        config=config,
        options={},
    )

    context = Context(user_id=None, parent_id=None, id="ctx-error")
    user_input = ConversationInput(
        text="please help",
        context=context,
        conversation_id="conv-error",
        device_id=None,
        language="en-US",
        agent_id=None,
    )

    result = await agent.async_process(user_input)
    response = result.response

    assert result.conversation_id == "conv-error"
    assert response.response_type == IntentResponseType.ERROR
    assert response.error_code == IntentResponseErrorCode.UNKNOWN
    assert response.speech["plain"]["speech"] == "Upstream failure"


@pytest.mark.asyncio
async def test_async_process_handles_error_payload() -> None:
    """Error payloads from the service produce error responses."""

    hass = _StubHass()
    config = IntegrationConfig(
        endpoint="https://example.invalid/api",
        auth_token=None,
        headers={},
        exposure=[],
        throttle=None,
        batching=False,
        routing={},
    )

    response_payload = {
        "conversation_id": "conv-upstream",
        "error": {"code": "failed_to_handle", "message": "Cannot comply"},
    }

    client = _RecordingClient(response_payload)
    agent = AIEmbodiedConversationAgent(
        hass=hass,
        entry_id="entry-3",
        client=client,
        config=config,
        options={},
    )

    context = Context(user_id="user-42", parent_id=None, id="ctx-upstream")
    user_input = ConversationInput(
        text="do something",
        context=context,
        conversation_id="conv-request",
        device_id="device-2",
        language="en-US",
        agent_id=None,
    )

    result = await agent.async_process(user_input)
    response = result.response

    assert result.conversation_id == "conv-upstream"
    assert response.response_type == IntentResponseType.ERROR
    assert response.error_code == IntentResponseErrorCode.FAILED_TO_HANDLE
    assert response.speech["plain"]["speech"] == "Cannot comply"
