"""Tests for the config and options flows."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from custom_components.aiembodied.config_flow import (
    AIEmbodiedConfigFlow,
    AIEmbodiedOptionsFlowHandler,
    _normalize_config_data,
    _parse_mapping,
)
from custom_components.aiembodied.const import (
    CONF_AUTH_TOKEN,
    CONF_BATCHING,
    CONF_ENDPOINT,
    CONF_EXPOSURE,
    CONF_HEADERS,
    CONF_ROUTING,
    CONF_THROTTLE,
    DOMAIN,
    OPTIONS_AUTONOMY_PAUSED,
    OPTIONS_BURST_SIZE,
    OPTIONS_DEBUG,
    OPTIONS_MAX_EVENTS_PER_MINUTE,
)
from homeassistant.data_entry_flow import FlowResultType


@dataclass
class _DummyConfigEntry:
    """Minimal representation of a Home Assistant config entry."""

    unique_id: str
    data: dict[str, object]
    domain: str = DOMAIN
    options: dict[str, object] = field(default_factory=dict)


class _DummyConfigEntries:
    """Collection of config entries scoped to the integration domain."""

    def __init__(self) -> None:
        self._entries: list[_DummyConfigEntry] = []
        self.flow = _DummyFlowManager()

    def async_entries(self, domain: str | None = None) -> list[_DummyConfigEntry]:
        if domain and domain != DOMAIN:
            return []
        return list(self._entries)

    def add(self, entry: _DummyConfigEntry) -> None:
        self._entries.append(entry)

    def async_entry_for_domain_unique_id(
        self, domain: str, unique_id: str
    ) -> _DummyConfigEntry | None:
        if domain != DOMAIN:
            return None
        for entry in self._entries:
            if entry.unique_id == unique_id:
                return entry
        return None


class _DummyFlowManager:
    """Flow manager stub returning no in-progress flows."""

    def async_progress_by_handler(
        self,
        handler: str,
        *,
        include_uninitialized: bool = False,
        match_context: dict[str, object] | None = None,
    ) -> list[dict[str, object]]:  # noqa: ARG002 - signature compatibility
        return []


class _DummyHass:
    """Lightweight Home Assistant stand-in for config flow tests."""

    def __init__(self) -> None:
        self.config_entries = _DummyConfigEntries()


@pytest.mark.asyncio
async def test_user_flow_creates_entry() -> None:
    """A successful user flow normalizes values and stores them in the entry."""

    hass = _DummyHass()
    flow = AIEmbodiedConfigFlow()
    flow.hass = hass  # type: ignore[assignment]
    flow.context = {}
    flow.handler = DOMAIN

    form = await flow.async_step_user()
    assert form["type"] == FlowResultType.FORM

    result = await flow.async_step_user(
        {
            CONF_ENDPOINT: "https://example.invalid/api",
            CONF_AUTH_TOKEN: "token-123",
            CONF_HEADERS: '{"X-Test": "1"}',
            CONF_EXPOSURE: "light.kitchen, sensor.office",
            CONF_THROTTLE: 45,
            CONF_BATCHING: False,
            CONF_ROUTING: "pipeline: assist",
        }
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "https://example.invalid/api"

    data = result["data"]
    assert data[CONF_ENDPOINT] == "https://example.invalid/api"
    assert data[CONF_AUTH_TOKEN] == "token-123"
    assert data[CONF_HEADERS] == {"X-Test": "1"}
    assert data[CONF_EXPOSURE] == ["light.kitchen", "sensor.office"]
    assert data[CONF_THROTTLE] == 45
    assert data[CONF_BATCHING] is False
    assert data[CONF_ROUTING] == {"pipeline": "assist"}

    hass.config_entries.add(_DummyConfigEntry(unique_id="https://example.invalid/api", data=data))

    flow_duplicate = AIEmbodiedConfigFlow()
    flow_duplicate.hass = hass  # type: ignore[assignment]
    flow_duplicate.context = {}
    flow_duplicate.handler = DOMAIN
    duplicate = await flow_duplicate.async_step_user(
        {
            CONF_ENDPOINT: "https://example.invalid/api",
            CONF_AUTH_TOKEN: "token-123",
            CONF_HEADERS: "X-Test: 1",
            CONF_EXPOSURE: "light.kitchen",
            CONF_THROTTLE: 60,
            CONF_BATCHING: True,
            CONF_ROUTING: "",
        }
    )
    assert duplicate["type"] == FlowResultType.ABORT
    assert duplicate["reason"] == "already_configured"


@pytest.mark.asyncio
async def test_user_flow_reports_invalid_headers() -> None:
    """Invalid header strings surface as inline form errors."""

    hass = _DummyHass()
    flow = AIEmbodiedConfigFlow()
    flow.hass = hass  # type: ignore[assignment]

    response = await flow.async_step_user(
        {
            CONF_ENDPOINT: "https://example.invalid/api",
            CONF_HEADERS: "bad-header",
            CONF_THROTTLE: 60,
        }
    )

    assert response["type"] == FlowResultType.FORM
    assert response["errors"][CONF_HEADERS] == "invalid_headers"


@pytest.mark.asyncio
async def test_options_flow_updates_values() -> None:
    """Options flow accepts positive integers and toggles."""

    entry = _DummyConfigEntry(unique_id="uid-1", data={}, options={})
    flow = AIEmbodiedOptionsFlowHandler(entry)

    form = await flow.async_step_init()
    assert form["type"] == FlowResultType.FORM

    result = await flow.async_step_init(
        {
            OPTIONS_DEBUG: True,
            OPTIONS_MAX_EVENTS_PER_MINUTE: 180,
            OPTIONS_BURST_SIZE: 12,
            OPTIONS_AUTONOMY_PAUSED: True,
        }
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    options = result["data"]
    assert options[OPTIONS_DEBUG] is True
    assert options[OPTIONS_MAX_EVENTS_PER_MINUTE] == 180
    assert options[OPTIONS_BURST_SIZE] == 12
    assert options[OPTIONS_AUTONOMY_PAUSED] is True


@pytest.mark.asyncio
async def test_options_flow_validates_positive_ints() -> None:
    """Invalid numeric inputs return field-specific errors."""

    entry = _DummyConfigEntry(unique_id="uid-2", data={}, options={})
    flow = AIEmbodiedOptionsFlowHandler(entry)

    response = await flow.async_step_init(
        {
            OPTIONS_DEBUG: False,
            OPTIONS_MAX_EVENTS_PER_MINUTE: 0,
            OPTIONS_BURST_SIZE: -3,
            OPTIONS_AUTONOMY_PAUSED: False,
        }
    )

    assert response["type"] == FlowResultType.FORM
    assert response["errors"][OPTIONS_MAX_EVENTS_PER_MINUTE] == "invalid_positive_int"
    assert response["errors"][OPTIONS_BURST_SIZE] == "invalid_positive_int"


@pytest.mark.asyncio
async def test_user_flow_requires_endpoint() -> None:
    """Missing endpoints surface as required field errors."""

    hass = _DummyHass()
    flow = AIEmbodiedConfigFlow()
    flow.hass = hass  # type: ignore[assignment]

    response = await flow.async_step_user({CONF_THROTTLE: 60})
    assert response["type"] == FlowResultType.FORM
    assert response["errors"][CONF_ENDPOINT] == "required"


@pytest.mark.asyncio
async def test_user_flow_validates_throttle_values() -> None:
    """Throttle values must be positive integers."""

    hass = _DummyHass()
    flow = AIEmbodiedConfigFlow()
    flow.hass = hass  # type: ignore[assignment]

    invalid_type = await flow.async_step_user(
        {CONF_ENDPOINT: "https://example.invalid", CONF_THROTTLE: "abc"}
    )
    assert invalid_type["type"] == FlowResultType.FORM
    assert invalid_type["errors"][CONF_THROTTLE] == "invalid_throttle"

    non_positive = await flow.async_step_user(
        {CONF_ENDPOINT: "https://example.invalid", CONF_THROTTLE: 0}
    )
    assert non_positive["type"] == FlowResultType.FORM
    assert non_positive["errors"][CONF_THROTTLE] == "invalid_throttle"


def test_parse_mapping_variants() -> None:
    """Mapping parser supports multiple input formats and errors."""

    assert _parse_mapping({"A": 1}) == {"A": "1"}
    assert _parse_mapping("foo: 1\n\nbar: 2") == {"foo": "1", "bar": "2"}

    with pytest.raises(ValueError):
        _parse_mapping(": missing")

    with pytest.raises(ValueError):
        _parse_mapping('["not", "a", "mapping"]')


def test_normalize_config_data_coerces_values() -> None:
    """Normalization coerces optional fields and parses collections."""

    data = _normalize_config_data(
        {
            CONF_ENDPOINT: " https://example.invalid ",
            CONF_AUTH_TOKEN: "  ",
            CONF_HEADERS: {"X-Test": 1},
            CONF_EXPOSURE: ["light.kitchen", ""],
            CONF_ROUTING: "pipeline: assist",
            CONF_THROTTLE: 5,
            CONF_BATCHING: False,
        }
    )

    assert data[CONF_ENDPOINT] == "https://example.invalid"
    assert data[CONF_AUTH_TOKEN] is None
    assert data[CONF_HEADERS] == {"X-Test": "1"}
    assert data[CONF_EXPOSURE] == ["light.kitchen"]
    assert data[CONF_ROUTING] == {"pipeline": "assist"}
    assert data[CONF_THROTTLE] == 5
    assert data[CONF_BATCHING] is False
