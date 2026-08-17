"""Category-based mappings for documented ML floor-heating devices."""

from __future__ import annotations

import math
from collections.abc import Mapping

from ..models import MLControlCommand, MLDevice, MLFunction
from .base import UnsupportedHACommandError, ml_device_spec
from .models import HACommand, HADeviceSpec, HAEntitySpec, HAEntityState, MLCommands

_TRUE = "true"
_FALSE = "false"
_POWER = "switch"
_TARGET_TEMPERATURE = "settemp"
_CURRENT_TEMPERATURE = "curtemp"
_CURRENT_HUMIDITY = "hum"


class FloorHeatMapper:
    """Map documented floor heating to one HA climate entity.

    The ML category supplies an on/off point rather than a separately
    selectable operating mode. It is therefore represented by HA's ``off``
    and ``heat`` HVAC modes without inventing unsupported ML functionality.
    """

    classification = "floor_heat"
    target_temperature_limits = (0.0, 100.0)
    current_temperature_limits = (0.0, 100.0)
    include_current_humidity = False

    def map_device(self, device: MLDevice) -> HADeviceSpec:
        return ml_device_spec(device, self.classification)

    def map_entities(self, device: MLDevice) -> tuple[HAEntitySpec, ...]:
        functions = {function.fun_no: function for function in device.functions}
        power = self._boolean(functions.get(_POWER))
        if power is None or not power.readable:
            return ()
        consumed = [_POWER]
        commands: list[str] = []
        if power.writable:
            commands.extend(("turn_on", "turn_off", "set_hvac_mode"))
        target = self._number(
            functions.get(_TARGET_TEMPERATURE), *self.target_temperature_limits
        )
        if target is not None:
            consumed.append(_TARGET_TEMPERATURE)
            if target.writable:
                commands.append("set_temperature")
        if self._number(
            functions.get(_CURRENT_TEMPERATURE), *self.current_temperature_limits
        ) is not None:
            consumed.append(_CURRENT_TEMPERATURE)
        humidity = self._number(functions.get(_CURRENT_HUMIDITY), 0.0, 100.0)
        if self.include_current_humidity and humidity is not None and humidity.readable:
            consumed.append(_CURRENT_HUMIDITY)
        return (
            HAEntitySpec(
                device.c_dev_id,
                "climate",
                "climate",
                None,
                tuple(consumed),
                tuple(commands),
            ),
        )

    def map_state(
        self, device: MLDevice, states: Mapping[str, str]
    ) -> tuple[HAEntityState, ...]:
        if not self.map_entities(device):
            return ()
        power = states.get(_POWER)
        if power not in {_TRUE, _FALSE}:
            return (self._invalid_state(power),)
        functions = {function.fun_no: function for function in device.functions}
        attributes: dict[str, object] = {"hvac_modes": ("off", "heat")}
        target = self._number(
            functions.get(_TARGET_TEMPERATURE), *self.target_temperature_limits
        )
        if target is not None:
            value = self._number_value(
                states.get(_TARGET_TEMPERATURE), target, *self.target_temperature_limits
            )
            if value is not None:
                attributes["target_temperature"] = value
            limits = self._limits(target, *self.target_temperature_limits)
            if limits is not None:
                attributes["min_temp"], attributes["max_temp"] = limits
                attributes["target_temperature_step"] = 1.0
        current = self._number(
            functions.get(_CURRENT_TEMPERATURE), *self.current_temperature_limits
        )
        if current is not None:
            value = self._number_value(
                states.get(_CURRENT_TEMPERATURE),
                current,
                *self.current_temperature_limits,
            )
            if value is not None:
                attributes["current_temperature"] = value
        if self.include_current_humidity:
            humidity = self._number(functions.get(_CURRENT_HUMIDITY), 0.0, 100.0)
            if humidity is not None and humidity.readable:
                value = self._number_value(
                    states.get(_CURRENT_HUMIDITY), humidity, 0.0, 100.0
                )
                if value is not None:
                    attributes["current_humidity"] = value
        return (HAEntityState("climate", "heat" if power == _TRUE else "off", True, attributes),)

    def map_command(
        self, device: MLDevice, entity: HAEntitySpec, command: HACommand
    ) -> MLCommands:
        expected = self.map_entities(device)
        if not expected or entity != expected[0] or command.name not in entity.commands:
            raise UnsupportedHACommandError(
                f"unsupported floor-heating command {command.name!r}"
            )
        if command.name == "turn_on":
            return (MLControlCommand(device.c_dev_id, _POWER, _TRUE),)
        if command.name == "turn_off":
            return (MLControlCommand(device.c_dev_id, _POWER, _FALSE),)
        if command.name == "set_hvac_mode":
            if command.value == "heat":
                return (MLControlCommand(device.c_dev_id, _POWER, _TRUE),)
            if command.value == "off":
                return (MLControlCommand(device.c_dev_id, _POWER, _FALSE),)
            raise UnsupportedHACommandError("floor heating supports only off and heat")
        if command.name == "set_temperature":
            return (
                MLControlCommand(
                    device.c_dev_id,
                    _TARGET_TEMPERATURE,
                    self._temperature_command(device, command.value),
                ),
            )
        raise UnsupportedHACommandError(
            f"unsupported floor-heating command {command.name!r}"
        )

    @staticmethod
    def _boolean(function: MLFunction | None) -> MLFunction | None:
        if function is None:
            return None
        if function.range_values is not None and not {_TRUE, _FALSE}.issubset(
            function.range_values
        ):
            return None
        return function

    def _number(
        self, function: MLFunction | None, minimum: float, maximum: float
    ) -> MLFunction | None:
        if function is None or self._limits(function, minimum, maximum) is None:
            return None
        return function

    @staticmethod
    def _limits(
        function: MLFunction, minimum: float, maximum: float
    ) -> tuple[float, float] | None:
        try:
            lower = (
                float(function.minimum)
                if function.minimum
                else minimum
            )
            upper = (
                float(function.maximum)
                if function.maximum
                else maximum
            )
        except ValueError:
            return None
        if not all(math.isfinite(value) for value in (lower, upper)):
            return None
        lower = max(lower, minimum)
        upper = min(upper, maximum)
        return (lower, upper) if lower <= upper else None

    def _number_value(
        self, value: str | None, function: MLFunction, minimum: float, maximum: float
    ) -> float | None:
        limits = self._limits(function, minimum, maximum)
        if value is None or limits is None:
            return None
        try:
            number = float(value)
        except ValueError:
            return None
        if not math.isfinite(number) or not limits[0] <= number <= limits[1]:
            return None
        return number

    def _temperature_command(self, device: MLDevice, value: object | None) -> str:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise UnsupportedHACommandError("settemp must be numeric")
        function = next(
            (item for item in device.functions if item.fun_no == _TARGET_TEMPERATURE),
            None,
        )
        limits = (
            self._limits(function, *self.target_temperature_limits)
            if function is not None
            else None
        )
        number = float(value)
        if limits is None or not math.isfinite(number) or not limits[0] <= number <= limits[1]:
            raise UnsupportedHACommandError("settemp is outside the advertised range")
        return str(int(number)) if number.is_integer() else str(number)

    @staticmethod
    def _invalid_state(value: str | None) -> HAEntityState:
        attributes = {} if value is None else {"invalid_ml_value": value}
        return HAEntityState("climate", None, False, attributes)


class PanelFloorHeatMapper(FloorHeatMapper):
    """Map a floor-heating panel while preserving its ML business identity."""

    classification = "floor_heat_panel"
    target_temperature_limits = (16.0, 31.0)
    current_temperature_limits = (-10.0, 60.0)
    include_current_humidity = True
