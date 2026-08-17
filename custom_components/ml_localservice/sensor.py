"""Sensor platform for ML LocalService."""

from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import MLEntity
from .platform import async_setup_ml_platform
from .runtime import MLConfigEntryRuntime


class MLSensorEntity(MLEntity, SensorEntity):
    """One numeric ML function represented as an HA sensor."""

    @property
    def native_value(self) -> float | None:
        value = self._mapped_state.native_value
        return value if isinstance(value, float) else None

    @property
    def device_class(self) -> SensorDeviceClass | None:
        return SensorDeviceClass(self._spec.device_class) if self._spec.device_class else None

    @property
    def native_unit_of_measurement(self) -> str | None:
        return self._spec.native_unit_of_measurement

    @property
    def state_class(self) -> SensorStateClass | None:
        return SensorStateClass(self._spec.state_class) if self._spec.state_class else None


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry[MLConfigEntryRuntime], async_add_entities: AddEntitiesCallback) -> None:
    """Set up ML sensor entities for one gateway."""
    await async_setup_ml_platform(hass, entry, async_add_entities, "sensor", MLSensorEntity)
