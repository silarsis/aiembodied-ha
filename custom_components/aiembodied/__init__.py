"""Home Assistant integration for the Embodied AI assistant."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from aiohttp import ClientSession
from homeassistant.components import conversation as ha_conversation
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import aiohttp_client
from homeassistant.helpers.typing import ConfigType

from .api_client import AIEmbodiedClient, AIEmbodiedClientConfig
from .config_flow import AIEmbodiedOptionsFlowHandler
from .const import (
    CONF_AUTH_TOKEN,
    CONF_BATCHING,
    CONF_ENDPOINT,
    CONF_EXPOSURE,
    CONF_HEADERS,
    CONF_ROUTING,
    CONF_THROTTLE,
    DATA_RUNTIME,
    DOMAIN,
)
from .conversation import AIEmbodiedConversationAgent
from .exposure import ExposureController

TYPE_CHECKING = False


@dataclass(slots=True)
class IntegrationConfig:
    """Runtime configuration derived from a config entry."""

    endpoint: str
    auth_token: str | None
    headers: dict[str, str]
    exposure: list[str] = field(default_factory=list)
    throttle: int | None = None
    batching: bool = False
    routing: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class RuntimeData:
    """Container for per-config entry runtime resources."""

    client: AIEmbodiedClient
    config: IntegrationConfig
    agent: AIEmbodiedConversationAgent
    exposure: ExposureController | None


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the aiembodied integration."""

    hass.data.setdefault(DOMAIN, {})
    _async_register_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up aiembodied from a config entry."""

    hass.data.setdefault(DOMAIN, {})

    runtime_config = _create_integration_config(entry.data)
    client = _create_client(hass, runtime_config)
    agent = AIEmbodiedConversationAgent(client, runtime_config)
    exposure = ExposureController(hass, client, runtime_config, entry.entry_id)
    await exposure.async_setup()
    runtime = RuntimeData(
        client=client,
        config=runtime_config,
        agent=agent,
        exposure=exposure,
    )
    hass.data[DOMAIN][entry.entry_id] = {DATA_RUNTIME: runtime}

    ha_conversation.async_set_agent(hass, entry, agent)
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""

    ha_conversation.async_unset_agent(hass, entry)
    runtime_wrapper = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    runtime: RuntimeData | None = None
    if runtime_wrapper is not None:
        runtime = runtime_wrapper.get(DATA_RUNTIME)

    if runtime and runtime.exposure:
        await runtime.exposure.async_shutdown()
    return True


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options updates by reloading the config entry."""

    await hass.config_entries.async_reload(entry.entry_id)


def _create_client(hass: HomeAssistant, config: IntegrationConfig) -> AIEmbodiedClient:
    """Instantiate the API client for the integration."""

    session = _async_get_clientsession(hass)
    client_config = AIEmbodiedClientConfig(
        endpoint=config.endpoint,
        auth_token=config.auth_token,
        headers=config.headers,
    )
    return AIEmbodiedClient(session, client_config)


def _create_integration_config(entry_data: dict[str, Any]) -> IntegrationConfig:
    """Build the runtime configuration from entry data."""

    return IntegrationConfig(
        endpoint=entry_data[CONF_ENDPOINT],
        auth_token=entry_data.get(CONF_AUTH_TOKEN),
        headers=dict(entry_data.get(CONF_HEADERS, {})),
        exposure=list(entry_data.get(CONF_EXPOSURE, [])),
        throttle=entry_data.get(CONF_THROTTLE),
        batching=bool(entry_data.get(CONF_BATCHING, False)),
        routing=dict(entry_data.get(CONF_ROUTING, {})),
    )


async def async_get_options_flow(
    config_entry: ConfigEntry,
) -> AIEmbodiedOptionsFlowHandler:
    """Return the options flow handler."""

    return AIEmbodiedOptionsFlowHandler(config_entry)


def _async_get_clientsession(hass: HomeAssistant) -> ClientSession:
    """Retrieve the shared aiohttp client session."""

    return aiohttp_client.async_get_clientsession(hass)


SERVICE_SEND_CONVERSATION_TURN = "send_conversation_turn"

ATTR_ENTRY_ID = "entry_id"
ATTR_TEXT = "text"
ATTR_CONVERSATION_ID = "conversation_id"
ATTR_LANGUAGE = "language"
ATTR_DEVICE_ID = "device_id"
ATTR_CONTEXT_ID = "context_id"
ATTR_CONTEXT_USER_ID = "context_user_id"
ATTR_CONTEXT_PARENT_ID = "context_parent_id"


def _async_register_services(hass: HomeAssistant) -> None:
    """Register integration services once per Home Assistant instance."""

    if hass.services.has_service(DOMAIN, SERVICE_SEND_CONVERSATION_TURN):
        return

    async def _async_handle_send_conversation_turn(call: Any) -> Mapping[str, Any]:
        data = _validate_service_data(getattr(call, "data", {}))
        entry_id = data[ATTR_ENTRY_ID]
        runtime_wrapper = hass.data.get(DOMAIN, {}).get(entry_id)
        if not runtime_wrapper:
            raise HomeAssistantError(
                f"No aiembodied configuration found for entry_id '{entry_id}'"
            )

        runtime: RuntimeData | None = runtime_wrapper.get(DATA_RUNTIME)
        if runtime is None:
            raise HomeAssistantError(
                f"Integration for entry_id '{entry_id}' is not currently active"
            )

        conversation_input = _conversation_input_from_service_data(data)
        result = await runtime.agent.async_handle(conversation_input)

        response: dict[str, Any] = {"conversation_id": result.conversation_id}
        if result.response is not None:
            response["response"] = {
                "text": result.response.text,
                "language": result.response.language,
                "data": result.response.data,
            }
        return response

    hass.services.async_register(
        DOMAIN,
        SERVICE_SEND_CONVERSATION_TURN,
        _async_handle_send_conversation_turn,
        supports_response=True,
    )


def _conversation_input_from_service_data(
    data: Mapping[str, Any],
) -> ha_conversation.ConversationInput:
    """Build a ConversationInput instance from service data."""

    context = _context_from_service_data(data)
    return ha_conversation.ConversationInput(
        text=data[ATTR_TEXT],
        conversation_id=data.get(ATTR_CONVERSATION_ID),
        language=data.get(ATTR_LANGUAGE),
        device_id=data.get(ATTR_DEVICE_ID),
        context=context,
    )


def _context_from_service_data(data: Mapping[str, Any]) -> Context | None:
    """Create a Home Assistant context from service attributes."""

    context_kwargs: dict[str, str] = {}
    if context_id := data.get(ATTR_CONTEXT_ID):
        context_kwargs["id"] = context_id
    if user_id := data.get(ATTR_CONTEXT_USER_ID):
        context_kwargs["user_id"] = user_id
    if parent_id := data.get(ATTR_CONTEXT_PARENT_ID):
        context_kwargs["parent_id"] = parent_id
    if not context_kwargs:
        return None
    return Context(**context_kwargs)


def _validate_service_data(data: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the payload received via the helper service."""

    normalized: dict[str, Any] = {}

    entry_id = data.get(ATTR_ENTRY_ID)
    if not isinstance(entry_id, str) or not entry_id:
        raise HomeAssistantError("Service data must include a non-empty entry_id")
    normalized[ATTR_ENTRY_ID] = entry_id

    text = data.get(ATTR_TEXT)
    if not isinstance(text, str) or not text:
        raise HomeAssistantError("Service data must include a non-empty text value")
    normalized[ATTR_TEXT] = text

    for attr in (
        ATTR_CONVERSATION_ID,
        ATTR_LANGUAGE,
        ATTR_DEVICE_ID,
        ATTR_CONTEXT_ID,
        ATTR_CONTEXT_USER_ID,
        ATTR_CONTEXT_PARENT_ID,
    ):
        value = data.get(attr)
        if value is None:
            continue
        if not isinstance(value, str):
            raise HomeAssistantError(f"Attribute '{attr}' must be a string if provided")
        if value:
            normalized[attr] = value

    return normalized
