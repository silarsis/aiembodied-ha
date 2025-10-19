"""Home Assistant integration for the Embodied AI assistant."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from aiohttp import ClientSession
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
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


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the aiembodied integration."""

    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up aiembodied from a config entry."""

    hass.data.setdefault(DOMAIN, {})

    runtime_config = _create_integration_config(entry.data)
    client = _create_client(hass, runtime_config)
    hass.data[DOMAIN][entry.entry_id] = {
        DATA_RUNTIME: RuntimeData(client=client, config=runtime_config)
    }

    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""

    hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
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
