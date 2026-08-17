"""Binary sensor platform for ML LocalService."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import MLEntity
from .platform import async_setup_ml_platform
from .runtime import MLConfigEntryRuntime


class MLBinarySensorEntity(MLEntity, BinarySensorEntity):
    """One boolean ML alarm, contact, or occupancy point."""

    @property
    def is_on(self) -> bool | None:
        value = self._mapped_state.native_value
        return value if isinstance(value, bool) else None

    @property
    def device_class(self) -> BinarySensorDeviceClass | None:
        return BinarySensorDeviceClass(self._spec.device_class) if self._spec.device_class else None


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry[MLConfigEntryRuntime], async_add_entities: AddEntitiesCallback) -> None:
    """Set up ML binary sensor entities for one gateway."""
    await async_setup_ml_platform(hass, entry, async_add_entities, "binary_sensor", MLBinarySensorEntity)
