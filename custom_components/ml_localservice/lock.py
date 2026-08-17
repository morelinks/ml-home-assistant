"""Lock platform for ML LocalService."""

from __future__ import annotations

from homeassistant.components.lock import LockEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import MLEntity
from .mapping import HACommand
from .platform import async_setup_ml_platform
from .runtime import MLConfigEntryRuntime


class MLLockEntity(MLEntity, LockEntity):
    """ML boolean door-lock state represented as an HA lock."""

    @property
    def is_locked(self) -> bool | None:
        value = self._mapped_state.native_value
        return value if isinstance(value, bool) else None

    async def async_lock(self, **_kwargs: object) -> None:
        await self.async_execute_command(HACommand("lock"))

    async def async_unlock(self, **_kwargs: object) -> None:
        await self.async_execute_command(HACommand("unlock"))


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[MLConfigEntryRuntime],
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up ML lock entities for one gateway."""
    await async_setup_ml_platform(hass, entry, async_add_entities, "lock", MLLockEntity)
