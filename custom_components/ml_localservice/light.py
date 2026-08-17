"""Light platform for ML LocalService."""

from __future__ import annotations

from typing import Any

from homeassistant.components.light import ColorMode, LightEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import MLEntity
from .mapping import HACommand
from .platform import async_setup_ml_platform
from .runtime import MLConfigEntryRuntime


class MLLightEntity(MLEntity, LightEntity):
    """ML lighting functions represented as one HA light."""

    @property
    def supported_color_modes(self) -> set[ColorMode]:
        fun_nos = set(self._spec.consumed_fun_nos)
        if "rgb" in fun_nos:
            return {ColorMode.RGB}
        if "temp" in fun_nos:
            return {ColorMode.COLOR_TEMP}
        if "bright" in fun_nos:
            return {ColorMode.BRIGHTNESS}
        return {ColorMode.ONOFF}

    @property
    def color_mode(self) -> ColorMode:
        return next(iter(self.supported_color_modes))

    @property
    def is_on(self) -> bool | None:
        value = self._mapped_state.native_value
        return value if isinstance(value, bool) else None

    @property
    def brightness(self) -> int | None:
        value = self._mapped_state.attributes.get("brightness")
        return value if isinstance(value, int) else None

    @property
    def color_temp_kelvin(self) -> int | None:
        value = self._mapped_state.attributes.get("color_temp_kelvin")
        return value if isinstance(value, int) else None

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:
        value = self._mapped_state.attributes.get("rgb_color")
        if isinstance(value, tuple) and len(value) == 3 and all(isinstance(item, int) for item in value):
            return value
        return None

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.async_execute_command(HACommand("turn_on"))
        if "brightness" in kwargs:
            await self.async_execute_command(HACommand("set_brightness", kwargs["brightness"]))
        if "color_temp_kelvin" in kwargs:
            await self.async_execute_command(HACommand("set_color_temp", kwargs["color_temp_kelvin"]))
        if "rgb_color" in kwargs:
            await self.async_execute_command(HACommand("set_rgb_color", kwargs["rgb_color"]))
        if "flash" in kwargs and "flash" in self._spec.commands:
            await self.async_execute_command(HACommand("flash", kwargs["flash"]))

    async def async_turn_off(self, **_kwargs: Any) -> None:
        await self.async_execute_command(HACommand("turn_off"))


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[MLConfigEntryRuntime],
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up ML light entities for one gateway."""
    await async_setup_ml_platform(
        hass,
        entry,
        async_add_entities,
        "light",
        MLLightEntity,
    )
