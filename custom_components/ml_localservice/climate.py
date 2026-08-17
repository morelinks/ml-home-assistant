"""Climate platform for ML LocalService air conditioners."""

from __future__ import annotations

from typing import Any

from homeassistant.components.climate import ClimateEntity, ClimateEntityFeature, HVACMode
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import MLEntity
from .mapping import HACommand
from .platform import async_setup_ml_platform
from .runtime import MLConfigEntryRuntime


class MLClimateEntity(MLEntity, ClimateEntity):
    """One ML thermal device projected as a single HA climate entity."""

    @property
    def hvac_mode(self) -> HVACMode | None:
        value = self._mapped_state.native_value
        try:
            return HVACMode(value) if isinstance(value, str) else None
        except ValueError:
            return None

    @property
    def hvac_modes(self) -> list[HVACMode]:
        values = self._mapped_state.attributes.get("hvac_modes", ())
        modes: list[HVACMode] = []
        for value in values:
            try:
                modes.append(HVACMode(value))
            except (TypeError, ValueError):
                continue
        return modes

    @property
    def supported_features(self) -> ClimateEntityFeature:
        commands = set(self._spec.commands)
        features = ClimateEntityFeature(0)
        if "turn_on" in commands:
            features |= ClimateEntityFeature.TURN_ON
        if "turn_off" in commands:
            features |= ClimateEntityFeature.TURN_OFF
        if "set_temperature" in commands:
            features |= ClimateEntityFeature.TARGET_TEMPERATURE
        if "set_fan_mode" in commands:
            features |= ClimateEntityFeature.FAN_MODE
        if "set_swing_mode" in commands:
            features |= ClimateEntityFeature.SWING_MODE
        return features

    @property
    def target_temperature(self) -> float | None:
        return self._number_attribute("target_temperature")

    @property
    def current_temperature(self) -> float | None:
        return self._number_attribute("current_temperature")

    @property
    def current_humidity(self) -> float | None:
        return self._number_attribute("current_humidity")

    @property
    def min_temp(self) -> float:
        value = self._number_attribute("min_temp")
        return value if value is not None else 16.0

    @property
    def max_temp(self) -> float:
        value = self._number_attribute("max_temp")
        return value if value is not None else 30.0

    @property
    def target_temperature_step(self) -> float:
        value = self._number_attribute("target_temperature_step")
        return value if value is not None else 1.0

    @property
    def temperature_unit(self) -> str:
        return UnitOfTemperature.CELSIUS

    @property
    def fan_mode(self) -> str | None:
        value = self._mapped_state.attributes.get("fan_mode")
        return value if isinstance(value, str) else None

    @property
    def fan_modes(self) -> list[str] | None:
        return self._string_options("fan_modes")

    @property
    def swing_mode(self) -> str | None:
        value = self._mapped_state.attributes.get("swing_mode")
        return value if isinstance(value, str) else None

    @property
    def swing_modes(self) -> list[str] | None:
        return self._string_options("swing_modes")

    async def async_turn_on(self) -> None:
        await self.async_execute_command(HACommand("turn_on"))

    async def async_turn_off(self) -> None:
        await self.async_execute_command(HACommand("turn_off"))

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        await self.async_execute_command(HACommand("set_hvac_mode", hvac_mode.value))

    async def async_set_temperature(self, **kwargs: Any) -> None:
        value = kwargs.get(ATTR_TEMPERATURE)
        if value is None:
            return
        await self.async_execute_command(HACommand("set_temperature", value))

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        await self.async_execute_command(HACommand("set_fan_mode", fan_mode))

    async def async_set_swing_mode(self, swing_mode: str) -> None:
        await self.async_execute_command(HACommand("set_swing_mode", swing_mode))

    def _number_attribute(self, name: str) -> float | None:
        value = self._mapped_state.attributes.get(name)
        return float(value) if isinstance(value, (int, float)) else None

    def _string_options(self, name: str) -> list[str] | None:
        value = self._mapped_state.attributes.get(name)
        return list(value) if isinstance(value, tuple) and all(isinstance(item, str) for item in value) else None


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[MLConfigEntryRuntime],
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up ML climate entities for one gateway."""
    await async_setup_ml_platform(hass, entry, async_add_entities, "climate", MLClimateEntity)
