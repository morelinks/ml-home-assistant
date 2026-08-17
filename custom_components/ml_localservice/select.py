"""Select platform for ML LocalService enumerated controls."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import MLEntity
from .mapping import HACommand
from .platform import async_setup_ml_platform
from .runtime import MLConfigEntryRuntime


class MLSelectEntity(MLEntity, SelectEntity):
    """An ML enum whose options cannot be losslessly expressed by a switch."""

    @property
    def current_option(self) -> str | None:
        value = self._mapped_state.native_value
        return value if isinstance(value, str) else None

    @property
    def options(self) -> list[str]:
        values = self._mapped_state.attributes.get("select_options", ())
        return list(values) if isinstance(values, tuple) else []

    async def async_select_option(self, option: str) -> None:
        await self.async_execute_command(HACommand("select_option", option))


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[MLConfigEntryRuntime],
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up ML select entities for one gateway."""
    await async_setup_ml_platform(hass, entry, async_add_entities, "select", MLSelectEntity)
