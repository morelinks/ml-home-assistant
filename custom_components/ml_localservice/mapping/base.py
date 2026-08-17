"""Interfaces and shared helpers for pure ML-to-HA mappings."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from ..models import MLDevice
from .models import (
    HACommand,
    HADeviceSpec,
    HAEntitySpec,
    HAEntityState,
    MLCommands,
)


class MLMappingError(ValueError):
    """Base class for deterministic mapping input errors."""


class UnsupportedHACommandError(MLMappingError):
    """An entity does not support the requested HA command."""


class MLDeviceMapper(Protocol):
    """Stable extension surface implemented by every device mapper."""

    def map_device(self, device: MLDevice) -> HADeviceSpec: ...

    def map_entities(self, device: MLDevice) -> tuple[HAEntitySpec, ...]: ...

    def map_state(
        self,
        device: MLDevice,
        states: Mapping[str, str],
    ) -> tuple[HAEntityState, ...]: ...

    def map_command(
        self,
        device: MLDevice,
        entity: HAEntitySpec,
        command: HACommand,
    ) -> MLCommands: ...


def ml_device_spec(
    device: MLDevice,
    classification: str,
    *,
    diagnostics: Mapping[str, object] | None = None,
) -> HADeviceSpec:
    """Build common device metadata without inventing parent relationships."""
    return HADeviceSpec(
        c_dev_id=device.c_dev_id,
        name=device.name or device.c_dev_id,
        classification=classification,
        model=f"{device.main_type}/{device.sub_type}",
        diagnostics=diagnostics or {},
    )


def function_names(device: MLDevice) -> tuple[str, ...]:
    """Return the advertised function schema in stable wire order."""
    return tuple(function.fun_no for function in device.functions)
