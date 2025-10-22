"""Switch entities for the aiembodied integration."""

from __future__ import annotations

from typing import Any, Callable, cast

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory

from . import RuntimeData
from .const import DATA_RUNTIME, DOMAIN


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: Any,
) -> None:
    """Set up autonomy control switches."""

    runtime_wrapper = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if not runtime_wrapper:
        return
    runtime = cast(RuntimeData | None, runtime_wrapper.get(DATA_RUNTIME))
    if runtime is None:
        return

    async_add_entities([AIEmbodiedAutonomySwitch(entry, runtime)])


class AIEmbodiedAutonomySwitch(SwitchEntity):
    """Toggle upstream autonomy for the Embodied AI runtime."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_icon = "mdi:robot"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, entry: ConfigEntry, runtime: RuntimeData) -> None:
        self._entry = entry
        self._runtime = runtime
        self._remove_listener: Callable[[], None] | None = None
        self._attr_name = "Autonomy"

    @property
    def unique_id(self) -> str:
        return f"{self._entry.entry_id}_autonomy"

    @property
    def is_on(self) -> bool:
        return not self._runtime.autonomy.paused

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._runtime.autonomy.async_set_paused(False)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._runtime.autonomy.async_set_paused(True)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        def _update() -> None:
            self.async_write_ha_state()

        self._remove_listener = self._runtime.autonomy.async_add_listener(_update)

    async def async_will_remove_from_hass(self) -> None:
        await super().async_will_remove_from_hass()
        if self._remove_listener is not None:
            self._remove_listener()
            self._remove_listener = None
