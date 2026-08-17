"""Button platform for ML LocalService."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import MLEntity
from .mapping import HACommand
from .platform import async_setup_ml_platform
from .runtime import MLConfigEntryRuntime


class MLButtonEntity(MLEntity, ButtonEntity):
    """One self-resetting ML panel action represented as an HA button."""

    async def async_press(self) -> None:
        await self.async_execute_command(HACommand("press"))


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[MLConfigEntryRuntime],
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up ML panel action buttons for one gateway."""
    await async_setup_ml_platform(hass, entry, async_add_entities, "button", MLButtonEntity)
