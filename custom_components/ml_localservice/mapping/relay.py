"""Mapping for the observed basic switching-light category."""

from __future__ import annotations

from collections.abc import Mapping

from ..models import MLControlCommand, MLDevice, MLFunction
from .base import UnsupportedHACommandError, ml_device_spec
from .models import HACommand, HADeviceSpec, HAEntitySpec, HAEntityState, MLCommands

_TRUE = "true"
_FALSE = "false"


class RelayLightMapper:
    """Map one boolean ML function to one non-optimistic HA light entity."""

    def __init__(self, fun_no: str = "switch") -> None:
        if not fun_no:
            raise ValueError("fun_no must not be empty")
        self._fun_no = fun_no

    def map_device(self, device: MLDevice) -> HADeviceSpec:
        return ml_device_spec(device, "relay_light")

    def map_entities(self, device: MLDevice) -> tuple[HAEntitySpec, ...]:
        function = self._boolean_function(device)
        if function is None or not function.readable:
            return ()
        commands = ("turn_on", "turn_off") if function.writable else ()
        return (
            HAEntitySpec(
                c_dev_id=device.c_dev_id,
                platform="light",
                mapping_key="power",
                name=None,
                consumed_fun_nos=(self._fun_no,),
                commands=commands,
            ),
        )

    def map_state(
        self, device: MLDevice, states: Mapping[str, str]
    ) -> tuple[HAEntityState, ...]:
        if not self.map_entities(device):
            return ()
        value = states.get(self._fun_no)
        if value is None:
            return (HAEntityState("power", None, False),)
        if value not in {_TRUE, _FALSE}:
            return (
                HAEntityState(
                    "power",
                    None,
                    False,
                    {"invalid_ml_value": value},
                ),
            )
        return (HAEntityState("power", value == _TRUE, True),)

    def map_command(
        self, device: MLDevice, entity: HAEntitySpec, command: HACommand
    ) -> MLCommands:
        expected = self.map_entities(device)
        if not expected or entity != expected[0]:
            raise UnsupportedHACommandError(
                f"entity is not the power mapping for {device.c_dev_id}"
            )
        if command.name not in entity.commands:
            raise UnsupportedHACommandError(
                f"command {command.name!r} is unsupported by {entity.mapping_key}"
            )
        value = _TRUE if command.name == "turn_on" else _FALSE
        return (MLControlCommand(device.c_dev_id, self._fun_no, value),)

    def _boolean_function(self, device: MLDevice) -> MLFunction | None:
        function = next(
            (item for item in device.functions if item.fun_no == self._fun_no),
            None,
        )
        if function is None:
            return None
        allowed = function.range_values
        if allowed is not None and not {_TRUE, _FALSE}.issubset(allowed):
            return None
        return function
