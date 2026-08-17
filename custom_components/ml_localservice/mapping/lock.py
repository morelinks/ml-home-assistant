"""Mapping for documented electric door locks."""

from __future__ import annotations

from collections.abc import Mapping

from ..models import MLControlCommand, MLDevice, MLFunction
from .base import UnsupportedHACommandError, ml_device_spec
from .models import HACommand, HADeviceSpec, HAEntitySpec, HAEntityState, MLCommands

_TRUE = "true"
_FALSE = "false"


class LockMapper:
    """Map the documented ``status`` boolean; true denotes a locked door."""

    _fun_no = "status"

    def map_device(self, device: MLDevice) -> HADeviceSpec:
        return ml_device_spec(device, "lock")

    def map_entities(self, device: MLDevice) -> tuple[HAEntitySpec, ...]:
        function = self._function(device)
        if function is None or not function.readable:
            return ()
        commands = ("lock", "unlock") if function.writable else ()
        return (
            HAEntitySpec(device.c_dev_id, "lock", "lock", None, (self._fun_no,), commands),
        )

    def map_state(self, device: MLDevice, states: Mapping[str, str]) -> tuple[HAEntityState, ...]:
        if not self.map_entities(device):
            return ()
        value = states.get(self._fun_no)
        if value not in {_TRUE, _FALSE}:
            attributes = {} if value is None else {"invalid_ml_value": value}
            return (HAEntityState("lock", None, False, attributes),)
        return (HAEntityState("lock", value == _TRUE, True),)

    def map_command(self, device: MLDevice, entity: HAEntitySpec, command: HACommand) -> MLCommands:
        expected = self.map_entities(device)
        if not expected or entity != expected[0] or command.name not in entity.commands:
            raise UnsupportedHACommandError("unsupported door-lock command")
        value = _TRUE if command.name == "lock" else _FALSE
        return (MLControlCommand(device.c_dev_id, self._fun_no, value),)

    def _function(self, device: MLDevice) -> MLFunction | None:
        function = next((item for item in device.functions if item.fun_no == self._fun_no), None)
        if function is None:
            return None
        if function.range_values is not None and not {_TRUE, _FALSE}.issubset(function.range_values):
            return None
        return function
