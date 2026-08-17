"""Zero-entity mappers for structural and placeholder ML devices."""

from __future__ import annotations

from collections.abc import Mapping

from ..models import MLDevice
from .base import UnsupportedHACommandError, function_names, ml_device_spec
from .models import HACommand, HADeviceSpec, HAEntitySpec, HAEntityState, MLCommands


class ZeroEntityMapper:
    """Describe a known non-entity device without producing log noise."""

    def __init__(self, classification: str) -> None:
        if classification not in {"container", "placeholder"}:
            raise ValueError("unsupported zero-entity classification")
        self._classification = classification

    def map_device(self, device: MLDevice) -> HADeviceSpec:
        return ml_device_spec(
            device,
            self._classification,
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
            f"{self._classification} device {device.c_dev_id} has no HA entity"
        )


class ContainerMapper(ZeroEntityMapper):
    def __init__(self) -> None:
        super().__init__("container")


class PlaceholderMapper(ZeroEntityMapper):
    def __init__(self) -> None:
        super().__init__("placeholder")
