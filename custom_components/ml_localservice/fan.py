"""Fan platform for ML LocalService ventilation devices."""

from __future__ import annotations

from typing import Any

from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import MLEntity
from .mapping import HACommand
from .platform import async_setup_ml_platform
from .runtime import MLConfigEntryRuntime


class MLFanEntity(MLEntity, FanEntity):
    """One ML ventilation unit represented as an HA fan."""

    @property
    def is_on(self) -> bool | None:
        value = self._mapped_state.native_value
        return value if isinstance(value, bool) else None

    @property
    def preset_mode(self) -> str | None:
        value = self._mapped_state.attributes.get("preset_mode")
        return value if isinstance(value, str) else None

    @property
    def preset_modes(self) -> list[str] | None:
        value = self._mapped_state.attributes.get("preset_modes")
        return list(value) if isinstance(value, tuple) else None

    @property
    def supported_features(self) -> FanEntityFeature:
        commands = set(self._spec.commands)
        features = FanEntityFeature(0)
        if "turn_on" in commands:
            features |= FanEntityFeature.TURN_ON
        if "turn_off" in commands:
            features |= FanEntityFeature.TURN_OFF
        if "set_preset_mode" in commands:
            features |= FanEntityFeature.PRESET_MODE
        return features

    async def async_turn_on(
        self,
        percentage: int | None = None,
        preset_mode: str | None = None,
        **_kwargs: Any,
    ) -> None:
        if percentage is not None:
            raise ValueError("ML ventilation fans do not support percentage speed")
        await self.async_execute_command(HACommand("turn_on"))
        if preset_mode is not None:
            await self.async_execute_command(HACommand("set_preset_mode", preset_mode))

    async def async_turn_off(self, **_kwargs: Any) -> None:
        await self.async_execute_command(HACommand("turn_off"))

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        await self.async_execute_command(HACommand("set_preset_mode", preset_mode))


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[MLConfigEntryRuntime],
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up ML fan entities for one gateway."""
    await async_setup_ml_platform(hass, entry, async_add_entities, "fan", MLFanEntity)
