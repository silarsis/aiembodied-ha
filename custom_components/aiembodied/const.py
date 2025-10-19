"""Constants for the aiembodied integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "aiembodied"
DATA_RUNTIME: Final = "runtime"
DEFAULT_TIMEOUT: Final = 10

CONF_ENDPOINT: Final = "endpoint"
CONF_AUTH_TOKEN: Final = "auth_token"
CONF_HEADERS: Final = "headers"
CONF_EXPOSURE: Final = "exposure"
CONF_THROTTLE: Final = "throttle"
CONF_BATCHING: Final = "batching"
CONF_ROUTING: Final = "routing"

OPTIONS_DEBUG: Final = "debug"
OPTIONS_MAX_EVENTS_PER_MINUTE: Final = "max_events_per_minute"
OPTIONS_BURST_SIZE: Final = "burst_size"
OPTIONS_AUTONOMY_PAUSED: Final = "autonomy_paused"

RUNTIME_DATA_CLIENT: Final = "client"
RUNTIME_DATA_CONFIG: Final = "config"
