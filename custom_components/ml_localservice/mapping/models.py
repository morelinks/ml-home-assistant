"""Home Assistant facing values produced by pure ML device mappers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from ..models import MLDevice, MLControlCommand


def _readonly(values: Mapping[str, object]) -> Mapping[str, object]:
    """Return a shallow immutable copy suitable for frozen mapping results."""
    return MappingProxyType(dict(values))


@dataclass(frozen=True, slots=True)
class HADeviceSpec:
    """Stable HA device-registry metadata independent of an HA runtime."""

    c_dev_id: str
    name: str
    classification: str
    manufacturer: str = "MoreLinks"
    model: str | None = None
    diagnostics: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "diagnostics", _readonly(self.diagnostics))

    def identifier(self, gateway_id: str) -> tuple[str, str]:
        """Build the stable HA Device Registry identifier."""
        if not gateway_id:
            raise ValueError("gateway_id must not be empty")
        return ("ml_localservice", f"{gateway_id}:{self.c_dev_id}")


@dataclass(frozen=True, slots=True)
class HAEntitySpec:
    """One stable HA entity projection of an ML device."""

    c_dev_id: str
    platform: str
    mapping_key: str
    name: str | None
    consumed_fun_nos: tuple[str, ...]
    commands: tuple[str, ...] = ()
    device_class: str | None = None
    native_unit_of_measurement: str | None = None
    state_class: str | None = None

    def __post_init__(self) -> None:
        if not self.platform or not self.mapping_key or not self.c_dev_id:
            raise ValueError("entity identity fields must not be empty")
        if not self.consumed_fun_nos:
            raise ValueError("an entity must consume at least one ML function")
        if len(set(self.consumed_fun_nos)) != len(self.consumed_fun_nos):
            raise ValueError("consumed_fun_nos must not contain duplicates")
        if len(set(self.commands)) != len(self.commands):
            raise ValueError("commands must not contain duplicates")

    def unique_id(self, gateway_id: str) -> str:
        """Build the stable HA Entity Registry unique ID."""
        if not gateway_id:
            raise ValueError("gateway_id must not be empty")
        return (
            f"{gateway_id}:{self.c_dev_id}:{self.platform}:{self.mapping_key}"
        )


@dataclass(frozen=True, slots=True)
class HAEntityState:
    """One mapped entity state without optimistic control updates."""

    mapping_key: str
    native_value: object
    available: bool
    attributes: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "attributes", _readonly(self.attributes))


@dataclass(frozen=True, slots=True)
class HACommand:
    """Platform-neutral command passed from an HA entity to its mapper."""

    name: str
    value: object | None = None

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("command name must not be empty")


@dataclass(frozen=True, slots=True)
class MappedDevice:
    """Registry description of a device and all entities emitted for it."""

    source: MLDevice
    device: HADeviceSpec
    entities: tuple[HAEntitySpec, ...]
    mapper_key: str | None


MLCommands = tuple[MLControlCommand, ...]
