"""Cover platform for ML LocalService curtain devices."""

from __future__ import annotations

from typing import Any

from homeassistant.components.cover import (
    ATTR_POSITION,
    ATTR_TILT_POSITION,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import MLEntity
from .mapping import HACommand
from .platform import async_setup_ml_platform
from .runtime import MLConfigEntryRuntime


class MLCoverEntity(MLEntity, CoverEntity):
    """One ML curtain mechanism represented as an HA cover."""

    @property
    def current_cover_position(self) -> int | None:
        return self._percentage_attribute("current_cover_position")

    @property
    def current_cover_tilt_position(self) -> int | None:
        return self._percentage_attribute("current_cover_tilt_position")

    @property
    def is_closed(self) -> bool | None:
        value = self._mapped_state.attributes.get("is_closed")
        return value if isinstance(value, bool) else None

    @property
    def is_opening(self) -> bool | None:
        value = self._mapped_state.attributes.get("is_opening")
        return value if isinstance(value, bool) else None

    @property
    def is_closing(self) -> bool | None:
        value = self._mapped_state.attributes.get("is_closing")
        return value if isinstance(value, bool) else None

    @property
    def supported_features(self) -> CoverEntityFeature:
        commands = set(self._spec.commands)
        features = CoverEntityFeature(0)
        for command, feature in (
            ("open_cover", CoverEntityFeature.OPEN),
            ("close_cover", CoverEntityFeature.CLOSE),
            ("stop_cover", CoverEntityFeature.STOP),
            ("set_cover_position", CoverEntityFeature.SET_POSITION),
            ("open_cover_tilt", CoverEntityFeature.OPEN_TILT),
            ("close_cover_tilt", CoverEntityFeature.CLOSE_TILT),
            ("stop_cover_tilt", CoverEntityFeature.STOP_TILT),
            ("set_cover_tilt_position", CoverEntityFeature.SET_TILT_POSITION),
        ):
            if command in commands:
                features |= feature
        return features

    async def async_open_cover(self, **_kwargs: Any) -> None:
        await self.async_execute_command(HACommand("open_cover"))

    async def async_close_cover(self, **_kwargs: Any) -> None:
        await self.async_execute_command(HACommand("close_cover"))

    async def async_stop_cover(self, **_kwargs: Any) -> None:
        await self.async_execute_command(HACommand("stop_cover"))

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        await self.async_execute_command(
            HACommand("set_cover_position", kwargs.get(ATTR_POSITION))
        )

    async def async_open_cover_tilt(self, **_kwargs: Any) -> None:
        await self.async_execute_command(HACommand("open_cover_tilt"))

    async def async_close_cover_tilt(self, **_kwargs: Any) -> None:
        await self.async_execute_command(HACommand("close_cover_tilt"))

    async def async_stop_cover_tilt(self, **_kwargs: Any) -> None:
        await self.async_execute_command(HACommand("stop_cover_tilt"))

    async def async_set_cover_tilt_position(self, **kwargs: Any) -> None:
        await self.async_execute_command(
            HACommand("set_cover_tilt_position", kwargs.get(ATTR_TILT_POSITION))
        )

    def _percentage_attribute(self, name: str) -> int | None:
        value = self._mapped_state.attributes.get(name)
        return value if isinstance(value, int) and not isinstance(value, bool) else None


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[MLConfigEntryRuntime],
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up ML curtain covers for one gateway."""
    await async_setup_ml_platform(hass, entry, async_add_entities, "cover", MLCoverEntity)
