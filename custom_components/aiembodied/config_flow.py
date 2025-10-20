"""Config and options flows for the AI Embodied integration."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_HEADERS
from homeassistant.data_entry_flow import FlowResult

from .const import (
    CONF_AUTH_TOKEN,
    CONF_BATCHING,
    CONF_ENDPOINT,
    CONF_EXPOSURE,
    CONF_ROUTING,
    CONF_THROTTLE,
    DOMAIN,
    OPTIONS_AUTONOMY_PAUSED,
    OPTIONS_BURST_SIZE,
    OPTIONS_DEBUG,
    OPTIONS_MAX_EVENTS_PER_MINUTE,
)


class AIEmbodiedConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the initial configuration flow."""

    VERSION = 1

    def __init__(self) -> None:
        self._user_input: dict[str, Any] = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Collect connection, exposure, and routing details."""

        errors: dict[str, str] = {}
        if user_input is not None:
            self._user_input.update(user_input)

            try:
                data = _normalize_config_data(self._user_input)
            except _ConfigValidationError as err:
                errors[err.field] = err.reason
            else:
                existing_entry = self.hass.config_entries.async_entry_for_domain_unique_id(
                    self.handler, data[CONF_ENDPOINT]
                )
                if existing_entry:
                    return self.async_abort(reason="already_configured")
                await self.async_set_unique_id(data[CONF_ENDPOINT])
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=data[CONF_ENDPOINT],
                    data=data,
                )

        return self.async_show_form(
            step_id="user",
            data_schema=_build_user_schema(self._user_input),
            errors=errors,
        )

    async def async_step_import(
        self, user_input: dict[str, Any]
    ) -> FlowResult:  # pragma: no cover - exercised indirectly
        """Handle YAML import by delegating to the user step."""

        return await self.async_step_user(user_input)


class AIEmbodiedOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle the options flow for runtime tuning."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Allow adjusting throttles, burst sizing, and debug flags."""

        errors: dict[str, str] = {}
        if user_input is not None:
            debug = bool(user_input.get(OPTIONS_DEBUG, False))
            autonomy_paused = bool(user_input.get(OPTIONS_AUTONOMY_PAUSED, False))

            try:
                max_events = _coerce_positive_int(user_input.get(OPTIONS_MAX_EVENTS_PER_MINUTE))
            except ValueError:
                errors[OPTIONS_MAX_EVENTS_PER_MINUTE] = "invalid_positive_int"

            try:
                burst_size = _coerce_positive_int(user_input.get(OPTIONS_BURST_SIZE))
            except ValueError:
                errors[OPTIONS_BURST_SIZE] = "invalid_positive_int"

            if not errors:
                return self.async_create_entry(
                    title="",
                    data={
                        OPTIONS_DEBUG: debug,
                        OPTIONS_MAX_EVENTS_PER_MINUTE: max_events,
                        OPTIONS_BURST_SIZE: burst_size,
                        OPTIONS_AUTONOMY_PAUSED: autonomy_paused,
                    },
                )

        schema = _build_options_schema(self._config_entry.options)
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)


def _build_user_schema(current: Mapping[str, Any]) -> vol.Schema:
    """Construct the schema for the primary config step."""

    return vol.Schema(
        {
            vol.Required(CONF_ENDPOINT, default=current.get(CONF_ENDPOINT, "")): str,
            vol.Optional(CONF_AUTH_TOKEN, default=current.get(CONF_AUTH_TOKEN, "")): str,
            vol.Optional(CONF_HEADERS, default=current.get(CONF_HEADERS, "")): str,
            vol.Optional(CONF_EXPOSURE, default=current.get(CONF_EXPOSURE, "")): str,
            vol.Optional(CONF_THROTTLE, default=current.get(CONF_THROTTLE, 60)): int,
            vol.Optional(CONF_BATCHING, default=current.get(CONF_BATCHING, True)): bool,
            vol.Optional(CONF_ROUTING, default=current.get(CONF_ROUTING, "")): str,
        }
    )


def _build_options_schema(current: Mapping[str, Any]) -> vol.Schema:
    """Construct the schema for the options flow."""

    return vol.Schema(
        {
            vol.Optional(OPTIONS_DEBUG, default=current.get(OPTIONS_DEBUG, False)): bool,
            vol.Optional(
                OPTIONS_MAX_EVENTS_PER_MINUTE,
                default=current.get(OPTIONS_MAX_EVENTS_PER_MINUTE, 120),
            ): int,
            vol.Optional(
                OPTIONS_BURST_SIZE,
                default=current.get(OPTIONS_BURST_SIZE, 10),
            ): int,
            vol.Optional(
                OPTIONS_AUTONOMY_PAUSED,
                default=current.get(OPTIONS_AUTONOMY_PAUSED, False),
            ): bool,
        }
    )


class _ConfigValidationError(Exception):
    """Raised when user supplied configuration is invalid."""

    def __init__(self, field: str, reason: str) -> None:
        super().__init__(f"{field}: {reason}")
        self.field = field
        self.reason = reason


def _normalize_config_data(user_input: Mapping[str, Any]) -> dict[str, Any]:
    """Parse and validate the primary config flow input."""

    endpoint = str(user_input.get(CONF_ENDPOINT, "")).strip()
    if not endpoint:
        raise _ConfigValidationError(CONF_ENDPOINT, "required")

    headers_raw = user_input.get(CONF_HEADERS, "")
    try:
        headers = _parse_mapping(headers_raw)
    except ValueError as err:
        raise _ConfigValidationError(CONF_HEADERS, "invalid_headers") from err

    exposure_raw = user_input.get(CONF_EXPOSURE, "")
    exposure = _parse_string_collection(exposure_raw)

    routing_raw = user_input.get(CONF_ROUTING, "")
    try:
        routing = _parse_mapping(routing_raw)
    except ValueError as err:  # pragma: no cover - defensive (routing rarely manual)
        raise _ConfigValidationError(CONF_ROUTING, "invalid_routing") from err

    throttle = user_input.get(CONF_THROTTLE)
    try:
        throttle_value = int(throttle)
    except (TypeError, ValueError):
        raise _ConfigValidationError(CONF_THROTTLE, "invalid_throttle")
    if throttle_value <= 0:
        raise _ConfigValidationError(CONF_THROTTLE, "invalid_throttle")

    batching = bool(user_input.get(CONF_BATCHING, True))

    return {
        CONF_ENDPOINT: endpoint,
        CONF_AUTH_TOKEN: str(user_input.get(CONF_AUTH_TOKEN) or "").strip() or None,
        CONF_HEADERS: headers,
        CONF_EXPOSURE: exposure,
        CONF_THROTTLE: throttle_value,
        CONF_BATCHING: batching,
        CONF_ROUTING: routing,
    }


def _parse_mapping(value: Any) -> dict[str, str]:
    """Parse mapping style input from JSON or newline-delimited pairs."""

    if isinstance(value, Mapping):
        return {str(key): str(val) for key, val in value.items()}

    text = str(value or "").strip()
    if not text:
        return {}

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        pairs: dict[str, str] = {}
        for line in text.splitlines():
            if not line.strip():
                continue
            if ":" not in line:
                raise ValueError(f"Invalid mapping line: {line}")
            key, val = line.split(":", 1)
            key = key.strip()
            val = val.strip()
            if not key:
                raise ValueError("Empty key in mapping")
            pairs[key] = val
        return pairs
    else:
        if not isinstance(parsed, dict):
            raise ValueError("Expected mapping JSON")
        return {str(key): str(val) for key, val in parsed.items()}


def _parse_string_collection(value: Any) -> list[str]:
    """Parse comma or newline separated string values."""

    if isinstance(value, list):
        items = [str(item).strip() for item in value]
    else:
        text = str(value or "")
        separators = [",", "\n"]
        for sep in separators:
            text = text.replace(sep, "\n")
        items = [item.strip() for item in text.splitlines()]

    return [item for item in items if item]


def _coerce_positive_int(value: Any) -> int:
    """Convert a value to a positive integer or raise."""

    number = int(value)
    if number <= 0:
        raise ValueError("Expected positive integer")
    return number
