"""Category-based mappings for documented ML air conditioners."""

from __future__ import annotations

import math
from collections.abc import Mapping

from ..models import MLControlCommand, MLDevice, MLFunction
from .base import UnsupportedHACommandError, ml_device_spec
from .models import HACommand, HADeviceSpec, HAEntitySpec, HAEntityState, MLCommands

_TRUE = "true"
_FALSE = "false"
_POWER = "switch"
_MODE = "mode"
_TARGET_TEMPERATURE = "settemp"
_FAN_MODE = "windspeed"
_CURRENT_TEMPERATURE = "curtemp"
_CURRENT_HUMIDITY = "hum"
_SWING_MODE = "sweep"
_MUTE = "mute_mode"
_AUX_HEAT = "aux_heat"

_HVAC_MODES = {
    "0": "fan_only",
    "1": "heat",
    "2": "cool",
    "3": "auto",
    "4": "heat",
    "5": "heat",
    "6": "dry",
    "7": "dry",
    "8": "auto",
    "9": "cool",
    "10": "heat",
    "11": "cool",
    "12": "heat",
    "13": "heat",
    "14": "heat",
    "15": "off",
    "16": "off",
    "17": "cool",
    "18": "cool",
    "19": "cool",
    "20": "cool",
    "21": "heat",
    "22": "heat",
    "24": "heat",
    "25": "heat_cool",
    "26": "heat",
    "27": "cool",
    "28": "auto",
    "29": "fan_only",
    "30": "cool",
    "31": "heat",
    "32": "heat",
    "33": "heat_cool",
    "34": "heat",
}
_FAN_MODES = {
    "0": "low",
    "1": "medium",
    "2": "high",
    "3": "auto",
    "4": "off",
    "5": "very_low",
    "6": "medium_low",
    "7": "medium_high",
    "8": "very_high",
    "9": "circulate",
    "10": "quiet",
}
_SWING_MODES = {
    "0": "auto",
    "1": "angle_1",
    "2": "angle_2",
    "3": "angle_3",
    "4": "angle_4",
    "5": "angle_5",
    "16": "paused",
}
_AUX_HEAT_MODES = {"0": "auto", "1": "on", "2": "off"}


class AirConditionerMapper:
    """Map a documented ML air conditioner without assuming optional points.

    A single climate entity combines the main control and telemetry points.
    Mute and the three-state auxiliary heat setting are separate HA entities,
    because they cannot be represented losslessly by the Climate API.
    """

    def __init__(self, classification: str = "air_conditioner") -> None:
        self._classification = classification

    def map_device(self, device: MLDevice) -> HADeviceSpec:
        return ml_device_spec(device, self._classification)

    def map_entities(self, device: MLDevice) -> tuple[HAEntitySpec, ...]:
        functions = {item.fun_no: item for item in device.functions}
        power = self._boolean(functions.get(_POWER))
        entities: list[HAEntitySpec] = []
        mode = self._enum(functions.get(_MODE), _HVAC_MODES)
        if power is not None and power.readable and mode is not None:
            consumed = [_POWER]
            commands: list[str] = []
            if power.writable:
                commands.extend(("turn_on", "turn_off"))
            consumed.append(_MODE)
            if mode.writable and self._values(mode, _HVAC_MODES):
                commands.append("set_hvac_mode")
            target = self._number(functions.get(_TARGET_TEMPERATURE), 16, 30)
            if target is not None:
                consumed.append(_TARGET_TEMPERATURE)
                if target.writable:
                    commands.append("set_temperature")
            fan = self._enum(functions.get(_FAN_MODE), _FAN_MODES)
            if fan is not None:
                consumed.append(_FAN_MODE)
                if fan.writable and self._values(fan, _FAN_MODES):
                    commands.append("set_fan_mode")
            current_temperature = self._number(
                functions.get(_CURRENT_TEMPERATURE), -10, 40
            )
            if current_temperature is not None:
                consumed.append(_CURRENT_TEMPERATURE)
            current_humidity = self._number(functions.get(_CURRENT_HUMIDITY), 0, 100)
            if current_humidity is not None:
                consumed.append(_CURRENT_HUMIDITY)
            swing = self._enum(functions.get(_SWING_MODE), _SWING_MODES)
            if swing is not None:
                consumed.append(_SWING_MODE)
                if swing.writable and self._values(swing, _SWING_MODES):
                    commands.append("set_swing_mode")
            entities.append(
                HAEntitySpec(
                    device.c_dev_id,
                    "climate",
                    "climate",
                    None,
                    tuple(consumed),
                    tuple(commands),
                )
            )
        mute = self._boolean(functions.get(_MUTE))
        if mute is not None and mute.readable:
            entities.append(
                HAEntitySpec(
                    device.c_dev_id,
                    "switch",
                    "mute_mode",
                    "Mute mode",
                    (_MUTE,),
                    ("turn_on", "turn_off") if mute.writable else (),
                )
            )
        aux_heat = self._enum(functions.get(_AUX_HEAT), _AUX_HEAT_MODES)
        if aux_heat is not None and aux_heat.readable and self._values(aux_heat, _AUX_HEAT_MODES):
            entities.append(
                HAEntitySpec(
                    device.c_dev_id,
                    "select",
                    "aux_heat",
                    "Auxiliary heat",
                    (_AUX_HEAT,),
                    ("select_option",) if aux_heat.writable else (),
                )
            )
        return tuple(entities)

    def map_state(
        self, device: MLDevice, states: Mapping[str, str]
    ) -> tuple[HAEntityState, ...]:
        functions = {item.fun_no: item for item in device.functions}
        result: list[HAEntityState] = []
        for entity in self.map_entities(device):
            if entity.mapping_key == "climate":
                result.append(self._climate_state(functions, states))
            elif entity.mapping_key == "mute_mode":
                result.append(self._boolean_state("mute_mode", states.get(_MUTE)))
            else:
                function = functions[_AUX_HEAT]
                result.append(self._aux_heat_state(states.get(_AUX_HEAT), function))
        return tuple(result)

    def map_command(
        self, device: MLDevice, entity: HAEntitySpec, command: HACommand
    ) -> MLCommands:
        if entity not in self.map_entities(device) or command.name not in entity.commands:
            raise UnsupportedHACommandError(
                f"unsupported air-conditioner command {command.name!r}"
            )
        if entity.mapping_key == "mute_mode":
            value = _TRUE if command.name == "turn_on" else _FALSE
            return (MLControlCommand(device.c_dev_id, _MUTE, value),)
        if command.name == "turn_on":
            return (MLControlCommand(device.c_dev_id, _POWER, _TRUE),)
        if command.name == "turn_off":
            return (MLControlCommand(device.c_dev_id, _POWER, _FALSE),)
        if command.name == "set_hvac_mode":
            if command.value == "off":
                return (MLControlCommand(device.c_dev_id, _POWER, _FALSE),)
            raw_mode = self._raw_value(device, _MODE, _HVAC_MODES, command.value)
            return (MLControlCommand(device.c_dev_id, _MODE, raw_mode),)
        if command.name == "set_temperature":
            return (
                MLControlCommand(
                    device.c_dev_id,
                    _TARGET_TEMPERATURE,
                    self._number_command(device, _TARGET_TEMPERATURE, command.value, 16, 30),
                ),
            )
        if command.name == "set_fan_mode":
            return (
                MLControlCommand(
                    device.c_dev_id,
                    _FAN_MODE,
                    self._raw_value(device, _FAN_MODE, _FAN_MODES, command.value),
                ),
            )
        if command.name == "set_swing_mode":
            return (
                MLControlCommand(
                    device.c_dev_id,
                    _SWING_MODE,
                    self._raw_value(device, _SWING_MODE, _SWING_MODES, command.value),
                ),
            )
        if command.name == "select_option":
            return (
                MLControlCommand(
                    device.c_dev_id,
                    _AUX_HEAT,
                    self._raw_value(device, _AUX_HEAT, _AUX_HEAT_MODES, command.value),
                ),
            )
        raise UnsupportedHACommandError(
            f"unsupported air-conditioner command {command.name!r}"
        )

    def _climate_state(
        self, functions: Mapping[str, MLFunction], states: Mapping[str, str]
    ) -> HAEntityState:
        power = states.get(_POWER)
        if power not in {_TRUE, _FALSE}:
            return self._invalid_state("climate", power)
        attributes: dict[str, object] = {}
        mode = self._enum(functions.get(_MODE), _HVAC_MODES)
        mode_value = states.get(_MODE)
        if power == _FALSE:
            native_value = "off"
        elif mode is None or mode_value not in self._values(mode, _HVAC_MODES):
            return self._invalid_state("climate", mode_value)
        else:
            native_value = _HVAC_MODES[mode_value]
        if mode is not None:
            values = self._values(mode, _HVAC_MODES)
            attributes["hvac_modes"] = self._mapped_options(values, _HVAC_MODES, include_off=True)
        else:
            attributes["hvac_modes"] = ("off",)
        for fun_no, attribute, minimum, maximum in (
            (_TARGET_TEMPERATURE, "target_temperature", 16, 30),
            (_CURRENT_TEMPERATURE, "current_temperature", -10, 40),
            (_CURRENT_HUMIDITY, "current_humidity", 0, 100),
        ):
            function = self._number(functions.get(fun_no), minimum, maximum)
            if function is None:
                continue
            value = self._number_value(states.get(fun_no), function, minimum, maximum)
            if value is not None:
                attributes[attribute] = value
            if fun_no == _TARGET_TEMPERATURE:
                limits = self._limits(function, minimum, maximum)
                if limits is not None:
                    attributes["min_temp"], attributes["max_temp"] = limits
                    attributes["target_temperature_step"] = 1.0
        for fun_no, attribute, choices in (
            (_FAN_MODE, "fan", _FAN_MODES),
            (_SWING_MODE, "swing", _SWING_MODES),
        ):
            function = self._enum(functions.get(fun_no), choices)
            if function is None:
                continue
            raw_value = states.get(fun_no)
            if raw_value in self._values(function, choices):
                attributes[f"{attribute}_mode"] = choices[raw_value]
            attributes[f"{attribute}_modes"] = self._mapped_options(
                self._values(function, choices), choices
            )
        return HAEntityState("climate", native_value, True, attributes)

    @staticmethod
    def _function(device: MLDevice, fun_no: str) -> MLFunction | None:
        return next((item for item in device.functions if item.fun_no == fun_no), None)

    @staticmethod
    def _boolean(function: MLFunction | None) -> MLFunction | None:
        if function is None or (function.range_values is not None and not {_TRUE, _FALSE}.issubset(function.range_values)):
            return None
        return function

    @staticmethod
    def _enum(
        function: MLFunction | None, choices: Mapping[str, str]
    ) -> MLFunction | None:
        if function is None:
            return None
        if function.range_values is not None and not any(
            value in choices for value in function.range_values
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
            lower = float(function.minimum) if function.minimum else minimum
            upper = float(function.maximum) if function.maximum else maximum
        except ValueError:
            return None
        if not all(math.isfinite(value) for value in (lower, upper)):
            return None
        lower, upper = max(lower, minimum), min(upper, maximum)
        return (lower, upper) if lower <= upper else None

    @staticmethod
    def _values(function: MLFunction, choices: Mapping[str, str]) -> tuple[str, ...]:
        values = function.range_values or tuple(choices)
        return tuple(value for value in values if value in choices)

    @staticmethod
    def _mapped_options(
        values: tuple[str, ...], choices: Mapping[str, str], *, include_off: bool = False
    ) -> tuple[str, ...]:
        mapped = tuple(dict.fromkeys(choices[value] for value in values))
        if include_off:
            return ("off", *(value for value in mapped if value != "off"))
        return mapped

    @staticmethod
    def _invalid_state(mapping_key: str, value: str | None) -> HAEntityState:
        attributes = {} if value is None else {"invalid_ml_value": value}
        return HAEntityState(mapping_key, None, False, attributes)

    def _boolean_state(self, mapping_key: str, value: str | None) -> HAEntityState:
        if value not in {_TRUE, _FALSE}:
            return self._invalid_state(mapping_key, value)
        return HAEntityState(mapping_key, value == _TRUE, True)

    def _enum_state(
        self, mapping_key: str, value: str | None, choices: Mapping[str, str]
    ) -> HAEntityState:
        if value not in choices:
            return self._invalid_state(mapping_key, value)
        return HAEntityState(mapping_key, choices[value], True)

    def _aux_heat_state(self, value: str | None, function: MLFunction) -> HAEntityState:
        values = self._values(function, _AUX_HEAT_MODES)
        state = (
            self._enum_state("aux_heat", value, _AUX_HEAT_MODES)
            if value in values
            else self._invalid_state("aux_heat", value)
        )
        return HAEntityState(
            state.mapping_key,
            state.native_value,
            state.available,
            {"select_options": self._mapped_options(self._values(function, _AUX_HEAT_MODES), _AUX_HEAT_MODES)},
        )

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
        return number if math.isfinite(number) and limits[0] <= number <= limits[1] else None

    def _raw_value(
        self, device: MLDevice, fun_no: str, choices: Mapping[str, str], value: object | None
    ) -> str:
        if not isinstance(value, str):
            raise UnsupportedHACommandError(f"{fun_no} must be a string option")
        function = self._function(device, fun_no)
        if function is None:
            raise UnsupportedHACommandError(f"{fun_no} is not supported")
        values = self._values(function, choices)
        for raw_value in values:
            if choices[raw_value] == value:
                return raw_value
        raise UnsupportedHACommandError(f"unsupported {fun_no} option {value!r}")

    def _number_command(
        self, device: MLDevice, fun_no: str, value: object | None, minimum: float, maximum: float
    ) -> str:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise UnsupportedHACommandError(f"{fun_no} must be numeric")
        function = self._function(device, fun_no)
        if function is None:
            raise UnsupportedHACommandError(f"{fun_no} is not supported")
        limits = self._limits(function, minimum, maximum)
        number = float(value)
        if limits is None or not math.isfinite(number) or not limits[0] <= number <= limits[1]:
            raise UnsupportedHACommandError(
                f"{fun_no} must be between {minimum:g} and {maximum:g}"
            )
        return str(int(number)) if number.is_integer() else str(number)
