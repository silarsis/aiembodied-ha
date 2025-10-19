"""Config entry stubs used for typing and flow support."""

from __future__ import annotations

from typing import Any, Awaitable, Callable


class ConfigEntry:
    """Minimal stub of Home Assistant's ConfigEntry."""

    def __init__(self, entry_id: str, data: dict[str, Any]) -> None:
        self.entry_id = entry_id
        self.data = data

    def add_update_listener(
        self, listener: Callable[[Any], Awaitable[None]]
    ) -> Callable[[], None]:
        """Register an update listener (no-op in tests)."""

        def _remove() -> None:
            return None

        return _remove

    def async_on_unload(self, callback: Callable[[], None]) -> None:
        """Register an unload callback (ignored)."""


class ConfigFlow:
    """Simplified ConfigFlow base class."""

    VERSION = 1
    DOMAIN: str | None = None

    def __init_subclass__(cls, **kwargs: Any) -> None:
        cls.DOMAIN = kwargs.pop("domain", cls.DOMAIN)
        super().__init_subclass__(**kwargs)

    def __init__(self) -> None:
        self.hass: Any = None
        self.context: dict[str, Any] = {}
        self.handler: str = ""
        self._unique_id: str | None = None

    def async_show_form(
        self,
        *,
        step_id: str,
        data_schema: Any = None,
        errors: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        return {
            "type": "form",
            "step_id": step_id,
            "data_schema": data_schema,
            "errors": errors or {},
        }

    def async_abort(self, *, reason: str) -> dict[str, Any]:
        return {"type": "abort", "reason": reason}

    def async_create_entry(
        self,
        *,
        title: str,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        return {"type": "create_entry", "title": title, "data": data}

    async def async_set_unique_id(self, unique_id: str) -> None:
        self._unique_id = unique_id

    def _abort_if_unique_id_configured(self) -> None:
        return None


class OptionsFlow:
    """Simplified OptionsFlow base class."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        self.config_entry = config_entry

    def async_show_form(
        self,
        *,
        step_id: str,
        data_schema: Any = None,
        errors: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        return {
            "type": "form",
            "step_id": step_id,
            "data_schema": data_schema,
            "errors": errors or {},
        }

    def async_create_entry(
        self,
        *,
        data: dict[str, Any],
        title: str | None = None,
    ) -> dict[str, Any]:
        result = {"type": "create_entry", "data": data}
        if title is not None:
            result["title"] = title
        return result
