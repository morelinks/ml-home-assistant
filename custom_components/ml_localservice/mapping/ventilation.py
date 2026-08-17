"""Category-based mappings for documented ML ventilation devices."""

from __future__ import annotations

import math
from collections.abc import Mapping

from ..models import MLControlCommand, MLDevice, MLFunction
from .base import UnsupportedHACommandError, ml_device_spec
from .models import HACommand, HADeviceSpec, HAEntitySpec, HAEntityState, MLCommands

_TRUE = "true"
_FALSE = "false"
_POWER = "switch"
_FAN_SPEED = "windspeed"
_INDOOR_TEMPERATURE = "intemp"
_INDOOR_HUMIDITY = "inhum"
_FAN_SPEEDS = {"0": "low", "1": "medium", "2": "high"}


class VentilationMapper:
    """Map documented fresh-air devices without assuming optional telemetry."""

    def __init__(self, classification: str = "ventilation") -> None:
        self._classification = classification

    def map_device(self, device: MLDevice) -> HADeviceSpec:
        return ml_device_spec(device, self._classification)

    def map_entities(self, device: MLDevice) -> tuple[HAEntitySpec, ...]:
        functions = {function.fun_no: function for function in device.functions}
        entities: list[HAEntitySpec] = []
        power = self._boolean(functions.get(_POWER))
        if power is not None and power.readable:
            consumed = [_POWER]
            commands: list[str] = []
            if power.writable:
                commands.extend(("turn_on", "turn_off"))
            speed = self._speed(functions.get(_FAN_SPEED))
            if speed is not None:
                consumed.append(_FAN_SPEED)
                if speed.writable and self._speed_values(speed):
                    commands.append("set_preset_mode")
            entities.append(
                HAEntitySpec(
                    device.c_dev_id,
                    "fan",
                    "fan",
                    None,
                    tuple(consumed),
                    tuple(commands),
                )
            )
        for fun_no, mapping_key, name, device_class, unit, minimum, maximum in (
            (
                _INDOOR_TEMPERATURE,
                "indoor_temperature",
                "Indoor temperature",
                "temperature",
                "°C",
                -10.0,
                40.0,
            ),
            (
                _INDOOR_HUMIDITY,
                "indoor_humidity",
                "Indoor humidity",
                "humidity",
                "%",
                0.0,
                100.0,
            ),
        ):
            function = self._number(functions.get(fun_no), minimum, maximum)
            if function is not None and function.readable:
                entities.append(
                    HAEntitySpec(
                        device.c_dev_id,
                        "sensor",
                        mapping_key,
                        name,
                        (fun_no,),
                        device_class=device_class,
                        native_unit_of_measurement=unit,
                        state_class="measurement",
                    )
                )
        return tuple(entities)

    def map_state(
        self, device: MLDevice, states: Mapping[str, str]
    ) -> tuple[HAEntityState, ...]:
        functions = {function.fun_no: function for function in device.functions}
        result: list[HAEntityState] = []
        for entity in self.map_entities(device):
            if entity.mapping_key == "fan":
                result.append(self._fan_state(functions, states))
                continue
            function = functions[entity.consumed_fun_nos[0]]
            limits = (-10.0, 40.0) if entity.mapping_key == "indoor_temperature" else (0.0, 100.0)
            result.append(
                self._number_state(
                    entity.mapping_key,
                    states.get(entity.consumed_fun_nos[0]),
                    function,
                    *limits,
                )
            )
        return tuple(result)

    def map_command(
        self, device: MLDevice, entity: HAEntitySpec, command: HACommand
    ) -> MLCommands:
        expected = self.map_entities(device)
        fan = next((item for item in expected if item.mapping_key == "fan"), None)
        if entity != fan or command.name not in entity.commands:
            raise UnsupportedHACommandError(
                f"unsupported ventilation command {command.name!r}"
            )
        if command.name == "turn_on":
            return (MLControlCommand(device.c_dev_id, _POWER, _TRUE),)
        if command.name == "turn_off":
            return (MLControlCommand(device.c_dev_id, _POWER, _FALSE),)
        if command.name == "set_preset_mode":
            if not isinstance(command.value, str):
                raise UnsupportedHACommandError("fan preset mode must be a string")
            speed = next(
                (function for function in device.functions if function.fun_no == _FAN_SPEED),
                None,
            )
            if speed is None:
                raise UnsupportedHACommandError("fan speed is not supported")
            for raw_value in self._speed_values(speed):
                if _FAN_SPEEDS[raw_value] == command.value:
                    return (MLControlCommand(device.c_dev_id, _FAN_SPEED, raw_value),)
            raise UnsupportedHACommandError(
                f"unsupported fan preset mode {command.value!r}"
            )
        raise UnsupportedHACommandError(
            f"unsupported ventilation command {command.name!r}"
        )

    def _fan_state(
        self, functions: Mapping[str, MLFunction], states: Mapping[str, str]
    ) -> HAEntityState:
        power = states.get(_POWER)
        if power not in {_TRUE, _FALSE}:
            return self._invalid_state("fan", power)
        attributes: dict[str, object] = {}
        speed = self._speed(functions.get(_FAN_SPEED))
        if speed is not None:
            values = self._speed_values(speed)
            attributes["preset_modes"] = tuple(_FAN_SPEEDS[value] for value in values)
            value = states.get(_FAN_SPEED)
            if value in values:
                attributes["preset_mode"] = _FAN_SPEEDS[value]
        return HAEntityState("fan", power == _TRUE, True, attributes)

    @staticmethod
    def _boolean(function: MLFunction | None) -> MLFunction | None:
        if function is None:
            return None
        if function.range_values is not None and not {_TRUE, _FALSE}.issubset(
            function.range_values
        ):
            return None
        return function

    @staticmethod
    def _speed(function: MLFunction | None) -> MLFunction | None:
        if function is None:
            return None
        if function.range_values is not None and not any(
            value in _FAN_SPEEDS for value in function.range_values
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
    def _speed_values(function: MLFunction) -> tuple[str, ...]:
        values = function.range_values or tuple(_FAN_SPEEDS)
        return tuple(value for value in values if value in _FAN_SPEEDS)

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

    def _number_state(
        self,
        mapping_key: str,
        value: str | None,
        function: MLFunction,
        minimum: float,
        maximum: float,
    ) -> HAEntityState:
        limits = self._limits(function, minimum, maximum)
        if value is None or limits is None:
            return self._invalid_state(mapping_key, value)
        try:
            number = float(value)
        except ValueError:
            return self._invalid_state(mapping_key, value)
        if not math.isfinite(number) or not limits[0] <= number <= limits[1]:
            return self._invalid_state(mapping_key, value)
        return HAEntityState(mapping_key, number, True)

    @staticmethod
    def _invalid_state(mapping_key: str, value: str | None) -> HAEntityState:
        attributes = {} if value is None else {"invalid_ml_value": value}
        return HAEntityState(mapping_key, None, False, attributes)
