"""Passive Home Assistant events emitted by physical ML panel actions."""

from __future__ import annotations

from homeassistant.components.event import EventEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import MLEntity
from .models import MLDeviceState
from .platform import async_setup_ml_platform
from .runtime import MLConfigEntryRuntime

_EVENT_TYPES = {
    "click": "click",
    "dbclick": "double_click",
    "longpress": "long_press",
}


class MLEventEntity(MLEntity, EventEntity):
    """One ML panel's physical action stream."""

    @property
    def event_types(self) -> list[str]:
        """Return only the action types advertised by this panel."""
        return [
            _EVENT_TYPES[fun_no]
            for fun_no in self._spec.consumed_fun_nos
            if fun_no in _EVENT_TYPES
        ]

    async def async_added_to_hass(self) -> None:
        """Subscribe after entity registration so old snapshots are not replayed."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self._runtime.coordinator.async_add_event_listener(self._handle_ml_event)
        )

    @callback
    def _handle_ml_event(self, event: MLDeviceState) -> None:
        """Turn one live ML action notification into one HA event occurrence."""
        if (
            event.c_dev_id != self._spec.c_dev_id
            or event.fun_val != "true"
            or event.fun_no not in self._spec.consumed_fun_nos
        ):
            return
        event_type = _EVENT_TYPES.get(event.fun_no)
        if event_type is None:
            return
        self._trigger_event(event_type)
        self.async_write_ha_state()


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[MLConfigEntryRuntime],
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up physical ML panel action event entities for one gateway."""
    await async_setup_ml_platform(hass, entry, async_add_entities, "event", MLEventEntity)
