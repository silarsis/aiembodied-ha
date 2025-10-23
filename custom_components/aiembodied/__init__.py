"""Home Assistant integration for the Embodied AI assistant."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Mapping

from aiohttp import ClientSession
from homeassistant.components import conversation as ha_conversation
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import aiohttp_client
from homeassistant.helpers.typing import ConfigType

from .api_client import AIEmbodiedClient, AIEmbodiedClientConfig, AIEmbodiedClientError
from .autonomy import AutonomyController
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
    OPTIONS_AUTONOMY_PAUSED,
    OPTIONS_BURST_SIZE,
    OPTIONS_DEBUG,
    OPTIONS_MAX_EVENTS_PER_MINUTE,
    RUNTIME_DATA_AUTONOMY,
    RUNTIME_DATA_OPTIONS,
)
from .conversation import AIEmbodiedConversationAgent
from .exposure import ExposureController

TYPE_CHECKING = False


_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.SENSOR,
    Platform.SWITCH,
]


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
class IntegrationOptions:
    """Normalized configuration entry options."""

    debug: bool = False
    max_events_per_minute: int | None = None
    burst_size: int | None = None
    autonomy_paused: bool = False


@dataclass(slots=True)
class RuntimeData:
    """Container for per-config entry runtime resources."""

    client: AIEmbodiedClient
    config: IntegrationConfig
    agent: AIEmbodiedConversationAgent
    exposure: ExposureController | None
    options: IntegrationOptions
    autonomy: AutonomyController


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the aiembodied integration."""

    hass.data.setdefault(DOMAIN, {})
    _async_register_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up aiembodied from a config entry."""

    hass.data.setdefault(DOMAIN, {})

    options = _create_integration_options(entry.options)
    _apply_debug_logging(options)

    runtime_config = _create_integration_config(entry.data)
    client = _create_client(hass, runtime_config)
    agent = AIEmbodiedConversationAgent(client, runtime_config)
    autonomy = AutonomyController(
        hass,
        entry,
        initial_paused=options.autonomy_paused,
    )
    exposure = ExposureController(
        hass,
        client,
        runtime_config,
        entry.entry_id,
        autonomy,
    )
    autonomy.add_pause_callbacks([exposure.async_set_paused])
    await exposure.async_setup()
    await autonomy.async_set_paused(options.autonomy_paused, persist=False, force_notify=True)
    runtime = RuntimeData(
        client=client,
        config=runtime_config,
        agent=agent,
        exposure=exposure,
        options=options,
        autonomy=autonomy,
    )
    hass.data[DOMAIN][entry.entry_id] = {
        DATA_RUNTIME: runtime,
        RUNTIME_DATA_OPTIONS: options,
        RUNTIME_DATA_AUTONOMY: autonomy,
    }

    ha_conversation.async_set_agent(hass, entry, agent)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""

    ha_conversation.async_unset_agent(hass, entry)
    await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

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


def _create_integration_options(entry_options: Mapping[str, Any]) -> IntegrationOptions:
    """Normalize config entry options."""

    max_events = entry_options.get(OPTIONS_MAX_EVENTS_PER_MINUTE)
    if isinstance(max_events, int) and max_events > 0:
        normalized_max = max_events
    else:
        normalized_max = None

    burst_size = entry_options.get(OPTIONS_BURST_SIZE)
    if isinstance(burst_size, int) and burst_size > 0:
        normalized_burst = burst_size
    else:
        normalized_burst = None

    return IntegrationOptions(
        debug=bool(entry_options.get(OPTIONS_DEBUG, False)),
        max_events_per_minute=normalized_max,
        burst_size=normalized_burst,
        autonomy_paused=bool(entry_options.get(OPTIONS_AUTONOMY_PAUSED, False)),
    )


async def async_get_options_flow(
    config_entry: ConfigEntry,
) -> AIEmbodiedOptionsFlowHandler:
    """Return the options flow handler."""

    return AIEmbodiedOptionsFlowHandler(config_entry)


def _async_get_clientsession(hass: HomeAssistant) -> ClientSession:
    """Retrieve the shared aiohttp client session."""

    return aiohttp_client.async_get_clientsession(hass)


def _apply_debug_logging(options: IntegrationOptions) -> None:
    """Adjust module logging based on the debug option."""

    package_logger = logging.getLogger(__package__ or DOMAIN)
    if options.debug:
        package_logger.setLevel(logging.DEBUG)
    else:
        package_logger.setLevel(logging.NOTSET)


SERVICE_SEND_CONVERSATION_TURN = "send_conversation_turn"
SERVICE_INVOKE_SERVICE = "invoke_service"

ATTR_ENTRY_ID = "entry_id"
ATTR_TEXT = "text"
ATTR_CONVERSATION_ID = "conversation_id"
ATTR_LANGUAGE = "language"
ATTR_DEVICE_ID = "device_id"
ATTR_CONTEXT_ID = "context_id"
ATTR_CONTEXT_USER_ID = "context_user_id"
ATTR_CONTEXT_PARENT_ID = "context_parent_id"
ATTR_DOMAIN = "domain"
ATTR_SERVICE = "service"
ATTR_SERVICE_DATA = "service_data"
ATTR_TARGET = "target"
ATTR_CORRELATION_ID = "correlation_id"


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

    async def _async_handle_invoke_service(call: Any) -> Mapping[str, Any]:
        data = _validate_action_service_data(getattr(call, "data", {}))
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

        if runtime.autonomy.paused:
            raise HomeAssistantError(
                "Autonomy is currently paused for this Embodied AI instance"
            )

        context = _context_from_service_data(data)
        domain = data[ATTR_DOMAIN]
        service = data[ATTR_SERVICE]
        service_data = dict(data.get(ATTR_SERVICE_DATA, {}))
        target = data.get(ATTR_TARGET)
        correlation_id = data.get(ATTR_CORRELATION_ID)

        try:
            result = await hass.services.async_call(
                domain,
                service,
                service_data,
                blocking=True,
                return_response=True,
                target=target,
                context=context,
            )
        except HomeAssistantError as exc:
            success = False
            error_message = str(exc)
            result = None
        except Exception as exc:  # pragma: no cover - defensive safety net
            success = False
            error_message = str(exc)
            result = None
        else:
            success = True
            error_message = None

        audit: dict[str, Any] = {
            "entry_id": entry_id,
            "domain": domain,
            "service": service,
            "correlation_id": correlation_id,
            "success": success,
        }
        if target is not None:
            audit["target"] = target
        if error_message is not None:
            audit["error"] = error_message

        hass.bus.async_fire(f"{DOMAIN}.action_executed", audit)

        payload: dict[str, Any] = {
            "type": "action_result",
            "action": {
                "entry_id": entry_id,
                "domain": domain,
                "service": service,
                "service_data": service_data,
                "success": success,
                "result": result,
            },
        }
        if target is not None:
            payload["action"]["target"] = target
        if correlation_id is not None:
            payload["action"]["correlation_id"] = correlation_id

        context_dict = _serialize_context_for_action(context)
        if context_dict is not None:
            payload["action"]["context"] = context_dict

        if not success and error_message is not None:
            payload["action"]["error"] = error_message

        try:
            await runtime.client.async_post_json(payload)
        except AIEmbodiedClientError as exc:
            _LOGGER.warning(
                "Failed to report action result for %s.%s: %s", domain, service, exc, exc_info=True
            )
            await runtime.autonomy.record_failure("action_result", str(exc))
        else:
            runtime.autonomy.record_success()

        response: dict[str, Any] = {
            "success": success,
            "result": result,
        }
        if error_message is not None:
            response["error"] = error_message
        if correlation_id is not None:
            response["correlation_id"] = correlation_id
        return response

    hass.services.async_register(
        DOMAIN,
        SERVICE_INVOKE_SERVICE,
        _async_handle_invoke_service,
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
    parent_id = data.get(ATTR_CONTEXT_PARENT_ID)
    if parent_id:
        context_kwargs["parent_id"] = parent_id
    if not context_kwargs:
        return None

    try:
        return Context(**context_kwargs)
    except TypeError:
        # Fallback for stubbed Context implementations without parent_id support
        context_kwargs.pop("parent_id", None)
        context = Context(**context_kwargs)
        if parent_id:
            try:
                setattr(context, "parent_id", parent_id)
            except AttributeError:  # pragma: no cover - slots without parent_id attribute
                pass
        return context


def _serialize_context_for_action(context: Context | None) -> dict[str, str] | None:
    """Serialize context data for action result payloads."""

    if context is None:
        return None
    result: dict[str, str] = {}
    for attr in ("id", "user_id", "parent_id"):
        value = getattr(context, attr, None)
        if isinstance(value, str) and value:
            result[attr] = value
    return result or None


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


def _validate_action_service_data(data: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the payload for outbound service execution."""

    normalized: dict[str, Any] = {}

    entry_id = data.get(ATTR_ENTRY_ID)
    if not isinstance(entry_id, str) or not entry_id:
        raise HomeAssistantError("Service data must include a non-empty entry_id")
    normalized[ATTR_ENTRY_ID] = entry_id

    domain = data.get(ATTR_DOMAIN)
    if not isinstance(domain, str) or not domain:
        raise HomeAssistantError("Service data must include a non-empty domain")
    normalized[ATTR_DOMAIN] = domain

    service = data.get(ATTR_SERVICE)
    if not isinstance(service, str) or not service:
        raise HomeAssistantError("Service data must include a non-empty service")
    normalized[ATTR_SERVICE] = service

    service_data = data.get(ATTR_SERVICE_DATA)
    if service_data is None:
        normalized[ATTR_SERVICE_DATA] = {}
    elif isinstance(service_data, Mapping):
        normalized[ATTR_SERVICE_DATA] = dict(service_data)
    else:
        raise HomeAssistantError("Attribute 'service_data' must be a mapping if provided")

    target = data.get(ATTR_TARGET)
    if target is not None:
        if not isinstance(target, Mapping):
            raise HomeAssistantError("Attribute 'target' must be a mapping if provided")
        normalized[ATTR_TARGET] = dict(target)

    correlation_id = data.get(ATTR_CORRELATION_ID)
    if correlation_id is not None:
        if not isinstance(correlation_id, str):
            raise HomeAssistantError("Attribute 'correlation_id' must be a string if provided")
        if correlation_id:
            normalized[ATTR_CORRELATION_ID] = correlation_id

    for attr in (
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
