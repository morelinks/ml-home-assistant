"""Shared Home Assistant entity behavior for ML LocalService devices."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from .client import MLClientError
from .mapping import HACommand, HAEntitySpec, HAEntityState, MLMappingError
from .models import MLDevice
from .runtime import MLConfigEntryRuntime

_LOGGER = logging.getLogger(__name__)


class MLEntity(Entity):
    """Coordinator-backed base class shared by every ML HA platform."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self, runtime: MLConfigEntryRuntime, spec: HAEntitySpec
    ) -> None:
        self._runtime = runtime
        self._spec = spec
        self._attr_unique_id = spec.unique_id(runtime.gateway_id)
        self._attr_name = spec.name

    @property
    def device_info(self) -> DeviceInfo:
        """Group every projection of one ML device under one HA device."""
        device = self._device
        if device is None:
            return DeviceInfo(
                identifiers={
                    (
                        "ml_localservice",
                        f"{self._runtime.gateway_id}:{self._spec.c_dev_id}",
                    )
                },
                name=self._spec.c_dev_id,
                manufacturer="MoreLinks",
            )
        mapped = self._runtime.registry.describe(device)
        description = mapped.device
        return DeviceInfo(
            identifiers={description.identifier(self._runtime.gateway_id)},
            name=description.name,
            manufacturer=description.manufacturer,
            model=description.model,
        )

    @property
    def available(self) -> bool:
        """Require a synchronized gateway and a valid mapped state."""
        return (
            self._runtime.coordinator.data.synchronized
            and self._mapped_state.available
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Expose only bounded mapper-provided diagnostic attributes."""
        attributes = self._mapped_state.attributes
        return dict(attributes) if attributes else None

    async def async_added_to_hass(self) -> None:
        """Write state whenever availability or a raw ML value changes."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self._runtime.coordinator.async_add_listener(
                self._handle_coordinator_update
            )
        )

    def update_spec(self, spec: HAEntitySpec) -> None:
        """Refresh non-identity mapping metadata after a directory change."""
        if spec.unique_id(self._runtime.gateway_id) != self.unique_id:
            raise ValueError("cannot change an ML entity unique ID in place")
        self._spec = spec
        self._attr_name = spec.name
        if self.hass is not None:
            self.async_write_ha_state()

    async def async_execute_command(self, command: HACommand) -> None:
        """Translate and submit a platform-neutral command."""
        device = self._device
        if device is None:
            raise HomeAssistantError("The ML device is no longer available")
        try:
            mapper = self._runtime.registry.resolve(device)
            commands = mapper.map_command(device, self._spec, command)
            for ml_command in commands:
                await self._runtime.coordinator.async_control(ml_command)
        except (MLClientError, MLMappingError, TypeError, ValueError) as err:
            raise HomeAssistantError(
                f"ML device command failed: {err}"
            ) from err

    @property
    def _device(self) -> MLDevice | None:
        return self._runtime.coordinator.data.devices.get(self._spec.c_dev_id)

    @property
    def _mapped_state(self) -> HAEntityState:
        device = self._device
        if device is None:
            return HAEntityState(self._spec.mapping_key, None, False)
        try:
            mapper = self._runtime.registry.resolve(device)
            states = self._runtime.coordinator.data.states.get(device.c_dev_id, {})
            return next(
                (
                    state
                    for state in mapper.map_state(device, states)
                    if state.mapping_key == self._spec.mapping_key
                ),
                HAEntityState(self._spec.mapping_key, None, False),
            )
        except (KeyError, TypeError, ValueError):
            _LOGGER.debug(
                "Invalid mapped state for ML entity %s", self.unique_id, exc_info=True
            )
            return HAEntityState(self._spec.mapping_key, None, False)

    def _handle_coordinator_update(self) -> None:
        if self.hass is not None:
            self.async_write_ha_state()
