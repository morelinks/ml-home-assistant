"""Switch platform boundary for ML LocalService."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import MLEntity
from .mapping import HACommand
from .platform import async_setup_ml_platform
from .runtime import MLConfigEntryRuntime


class MLSwitchEntity(MLEntity, SwitchEntity):
    """Boolean ML switching function represented as an HA switch."""

    @property
    def is_on(self) -> bool | None:
        value = self._mapped_state.native_value
        return value if isinstance(value, bool) else None

    async def async_turn_on(self, **_kwargs: Any) -> None:
        await self.async_execute_command(HACommand("turn_on"))

    async def async_turn_off(self, **_kwargs: Any) -> None:
        await self.async_execute_command(HACommand("turn_off"))


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[MLConfigEntryRuntime],
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up ML switch entities for one gateway."""
    await async_setup_ml_platform(
        hass,
        entry,
        async_add_entities,
        "switch",
        MLSwitchEntity,
    )
