"""Entity exposure and event forwarding for the aiembodied integration."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Iterable

from homeassistant.core import Context, Event, HomeAssistant, State, callback
from homeassistant.helpers.event import MATCH_ALL, async_track_state_change_event

from .api_client import AIEmbodiedClient, AIEmbodiedClientError
from .autonomy import AutonomyController
from .const import DOMAIN

if TYPE_CHECKING:  # pragma: no cover - imported for typing only
    from . import IntegrationConfig


_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class _ExposureFilters:
    """Preprocessed filters for determining entity forwarding eligibility."""

    entities: set[str]
    domains: set[str]

    @classmethod
    def from_iterable(cls, exposure: Iterable[str]) -> "_ExposureFilters":
        entities: set[str] = set()
        domains: set[str] = set()

        for item in exposure:
            normalized = item.strip().lower()
            if not normalized:
                continue
            if normalized.endswith(".*"):
                domains.add(normalized[:-2])
                continue
            if "." not in normalized:
                domains.add(normalized)
                continue
            entities.add(normalized)

        return cls(entities=entities, domains=domains)

    def allows(self, entity_id: str) -> bool:
        entity_lower = entity_id.lower()
        if entity_lower in self.entities:
            return True
        domain = entity_lower.split(".", 1)[0]
        return domain in self.domains


class ExposureController:
    """Manage entity exposure listeners and forward updates upstream."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: AIEmbodiedClient,
        config: "IntegrationConfig",
        entry_id: str,
        autonomy: AutonomyController | None = None,
    ) -> None:
        self._hass = hass
        self._client = client
        self._config = config
        self._entry_id = entry_id
        self._filters = _ExposureFilters.from_iterable(config.exposure)
        self._unsubscribe: Callable[[], None] | None = None
        self._autonomy = autonomy
        self._paused = False

    async def async_setup(self) -> None:
        """Attach listeners when exposures are configured."""

        if not self._filters.entities and not self._filters.domains:
            return
        if self._unsubscribe is not None:
            return
        self._unsubscribe = async_track_state_change_event(
            self._hass, MATCH_ALL, self._handle_state_change
        )

    async def async_shutdown(self) -> None:
        """Tear down listeners when the config entry unloads."""

        if self._unsubscribe is None:
            return
        self._unsubscribe()
        self._unsubscribe = None

    async def async_set_paused(self, paused: bool) -> None:
        """Pause or resume state tracking."""

        if paused == self._paused:
            return
        self._paused = paused
        if paused:
            if self._unsubscribe is not None:
                self._unsubscribe()
                self._unsubscribe = None
            return

        if self._filters.entities or self._filters.domains:
            if self._unsubscribe is None:
                self._unsubscribe = async_track_state_change_event(
                    self._hass, MATCH_ALL, self._handle_state_change
                )

    @callback
    def _handle_state_change(self, event: Event) -> None:
        entity_id: str | None = event.data.get("entity_id")
        if not entity_id or not self._filters.allows(entity_id):
            return
        if self._paused:
            return

        old_state: State | None = event.data.get("old_state")
        new_state: State | None = event.data.get("new_state")

        payload = self._build_payload(entity_id, old_state, new_state, event.context)
        self._hass.async_create_task(self._async_forward(payload))

    async def _async_forward(self, payload: dict[str, Any]) -> None:
        """Send the normalized update to the upstream service and emit audit events."""

        audit: dict[str, Any] = {
            "entry_id": self._entry_id,
            "entity_id": payload["data"]["entity_id"],
            "domain": payload["data"]["domain"],
        }

        try:
            await self._client.async_post_json(payload)
        except AIEmbodiedClientError as exc:
            audit["success"] = False
            audit["error"] = str(exc)
            if self._autonomy is not None:
                await self._autonomy.record_failure("event_forward", str(exc))
            _LOGGER.warning(
                "Failed to forward update for %s: %s", audit["entity_id"], exc, exc_info=True
            )
        else:
            audit["success"] = True
            if self._autonomy is not None:
                self._autonomy.record_success()

        self._hass.bus.async_fire(f"{DOMAIN}.update_forwarded", audit)

    def _build_payload(
        self,
        entity_id: str,
        old_state: State | None,
        new_state: State | None,
        context: Context | None,
    ) -> dict[str, Any]:
        """Construct the structured payload sent to the upstream service."""

        domain = entity_id.split(".", 1)[0]
        friendly_name = self._determine_friendly_name(new_state, old_state)
        area = self._resolve_area(entity_id)

        data: dict[str, Any] = {
            "entry_id": self._entry_id,
            "entity_id": entity_id,
            "domain": domain,
            "friendly_name": friendly_name,
            "area": area,
            "state": {
                "old": self._serialize_state(old_state),
                "new": self._serialize_state(new_state),
            },
        }

        context_dict = self._serialize_context(context)
        if context_dict is not None:
            data["context"] = context_dict

        return {
            "type": "event",
            "event": "state_changed",
            "data": data,
        }

    @staticmethod
    def _determine_friendly_name(new_state: State | None, old_state: State | None) -> str | None:
        for candidate in (new_state, old_state):
            if candidate is None:
                continue
            if candidate.name:
                return candidate.name
        return None

    def _resolve_area(self, entity_id: str) -> str | None:
        try:
            from homeassistant.helpers import entity_registry as er  # type: ignore
        except ImportError:  # pragma: no cover - optional dependency in stub tests
            return None

        try:
            entity_registry = er.async_get(self._hass)
        except Exception:  # pragma: no cover - registry lookup unavailable in tests
            return None

        entry = entity_registry.async_get(entity_id)
        if entry is None or entry.area_id is None:
            return None

        try:
            from homeassistant.helpers import area_registry as ar  # type: ignore
        except ImportError:  # pragma: no cover - optional dependency in stub tests
            return entry.area_id

        try:
            area_registry = ar.async_get(self._hass)
        except Exception:  # pragma: no cover - registry lookup unavailable in tests
            return entry.area_id

        area = area_registry.async_get(entry.area_id)
        if area is None:
            return entry.area_id
        return area.name or area.id

    @staticmethod
    def _serialize_state(state: State | None) -> dict[str, Any] | None:
        if state is None:
            return None
        return {
            "state": state.state,
            "attributes": dict(state.attributes),
            "last_changed": state.last_changed.isoformat(),
            "last_updated": state.last_updated.isoformat(),
        }

    @staticmethod
    def _serialize_context(context: Context | None) -> dict[str, str] | None:
        if context is None:
            return None
        result: dict[str, str] = {}
        for attr in ("id", "user_id", "parent_id"):
            value = getattr(context, attr, None)
            if isinstance(value, str) and value:
                result[attr] = value
        return result or None
