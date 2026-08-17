"""Safe zero-entity fallback for unsupported ML device types."""

from __future__ import annotations

from collections.abc import Mapping

from ..models import MLDevice
from .base import UnsupportedHACommandError, function_names, ml_device_spec
from .models import HACommand, HADeviceSpec, HAEntitySpec, HAEntityState, MLCommands


class FallbackMapper:
    """Retain bounded diagnostics while emitting no unsupported entities."""

    def map_device(self, device: MLDevice) -> HADeviceSpec:
        return ml_device_spec(
            device,
            "unsupported",
            diagnostics={"functions": function_names(device)},
        )

    def map_entities(self, device: MLDevice) -> tuple[HAEntitySpec, ...]:
        del device
        return ()

    def map_state(
        self, device: MLDevice, states: Mapping[str, str]
    ) -> tuple[HAEntityState, ...]:
        del device, states
        return ()

    def map_command(
        self, device: MLDevice, entity: HAEntitySpec, command: HACommand
    ) -> MLCommands:
        del entity, command
        raise UnsupportedHACommandError(
            f"ML device type {device.main_type}/{device.sub_type} is unsupported"
        )
