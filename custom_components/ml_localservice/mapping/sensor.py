"""Mappings for documented ML security and environmental sensors."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass

from ..models import MLControlCommand, MLDevice, MLFunction
from .base import UnsupportedHACommandError, ml_device_spec
from .models import HACommand, HADeviceSpec, HAEntitySpec, HAEntityState, MLCommands

_TRUE = "true"
_FALSE = "false"
_ONE = "1"
_ZERO = "0"
_BOOLEAN_TRUE_VALUES = frozenset((_TRUE, _ONE))
_BOOLEAN_FALSE_VALUES = frozenset((_FALSE, _ZERO))


@dataclass(frozen=True, slots=True)
class _BinaryDefinition:
    fun_no: str
    mapping_key: str
    device_class: str


@dataclass(frozen=True, slots=True)
class _NumericDefinition:
    fun_no: str
    mapping_key: str
    name: str | None
    device_class: str | None
    unit: str | None
    minimum: float
    maximum: float


@dataclass(frozen=True, slots=True)
class _SwitchDefinition:
    fun_no: str
    mapping_key: str
    name: str
    off_value: str
    on_value: str


@dataclass(frozen=True, slots=True)
class _SensorDefinition:
    binary: _BinaryDefinition | None = None
    numeric: tuple[_NumericDefinition, ...] = ()
    switches: tuple[_SwitchDefinition, ...] = ()


_STATUS_TYPES: dict[str, _SensorDefinition] = {
    "3/10300002": _SensorDefinition(
        binary=_BinaryDefinition("status", "occupancy", "motion"),
        switches=(
            _SwitchDefinition("sensor_mode", "sensor_mode", "Sensor mode", "0", "1"),
        ),
    ),
    "3/10300003": _SensorDefinition(
        binary=_BinaryDefinition("status", "alarm", "motion")
    ),
    "3/10300004": _SensorDefinition(
        binary=_BinaryDefinition("status", "alarm", "smoke")
    ),
    "3/10300005": _SensorDefinition(
        binary=_BinaryDefinition("status", "alarm", "gas")
    ),
    "3/10300006": _SensorDefinition(
        binary=_BinaryDefinition("status", "alarm", "moisture")
    ),
    "3/10300007": _SensorDefinition(
        binary=_BinaryDefinition("status", "alarm", "safety")
    ),
    "3/10300008": _SensorDefinition(
        binary=_BinaryDefinition("status", "open", "door")
    ),
    "3/10300009": _SensorDefinition(
        binary=_BinaryDefinition("status", "open", "window")
    ),
    "3/10300014": _SensorDefinition(
        binary=_BinaryDefinition("status", "occupancy", "motion"),
        numeric=(
            _NumericDefinition("lux", "illuminance", "Illuminance", "illuminance", "lx", 0, 1200),
            _NumericDefinition("battery", "battery_voltage", "Battery voltage", "voltage", "mV", 0, 3300),
        ),
    ),
    "3/10300015": _SensorDefinition(
        numeric=(
            _NumericDefinition("temp", "temperature", None, "temperature", "°C", -10, 50),
        )
    ),
    "3/10300016": _SensorDefinition(
        numeric=(
            _NumericDefinition("hum", "humidity", None, "humidity", "%", 0, 100),
        )
    ),
    "3/10300017": _SensorDefinition(
        numeric=(
            _NumericDefinition("lux", "illuminance", None, "illuminance", "lx", 0, 1200),
        )
    ),
    "3/10300019": _SensorDefinition(
        numeric=(
            _NumericDefinition("pm25", "pm25", None, "pm25", "μg/m³", 0, 500),
        )
    ),
    "3/10300020": _SensorDefinition(
        numeric=(
            _NumericDefinition("tvoc", "tvoc", None, None, None, 0, 4884),
        )
    ),
    "3/10300021": _SensorDefinition(
        numeric=(
            _NumericDefinition("ppm", "carbon_dioxide", None, "carbon_dioxide", "ppm", 0, 5000),
        )
    ),
    "3/10300023": _SensorDefinition(
        numeric=(
            _NumericDefinition("hpa", "atmospheric_pressure", None, "atmospheric_pressure", "hPa", 0, 2000),
        )
    ),
}


class SensorMapper:
    """Map one documented category-3 device without assuming its schema.

    Categories describe possible points, but a projection is emitted only for
    a readable point that the particular device advertises. This lets partial
    or variant devices remain safely usable.
    """

    def __init__(self, definition: _SensorDefinition) -> None:
        self._definition = definition

    @classmethod
    def for_type(cls, device_type: str) -> "SensorMapper":
        try:
            return cls(_STATUS_TYPES[device_type])
        except KeyError as err:
            raise ValueError(f"unsupported documented sensor type {device_type}") from err

    def map_device(self, device: MLDevice) -> HADeviceSpec:
        return ml_device_spec(device, "sensor")

    def map_entities(self, device: MLDevice) -> tuple[HAEntitySpec, ...]:
        functions = {function.fun_no: function for function in device.functions}
        entities: list[HAEntitySpec] = []
        binary = self._definition.binary
        if binary is not None and self._boolean(functions.get(binary.fun_no)):
            entities.append(
                HAEntitySpec(
                    device.c_dev_id,
                    "binary_sensor",
                    binary.mapping_key,
                    None,
                    (binary.fun_no,),
                    device_class=binary.device_class,
                )
            )
        for metric in self._definition.numeric:
            function = functions.get(metric.fun_no)
            if function is not None and function.readable and self._limits(function, metric) is not None:
                entities.append(
                    HAEntitySpec(
                        device.c_dev_id,
                        "sensor",
                        metric.mapping_key,
                        metric.name,
                        (metric.fun_no,),
                        device_class=metric.device_class,
                        native_unit_of_measurement=metric.unit,
                        state_class="measurement",
                    )
                )
        for switch in self._definition.switches:
            function = functions.get(switch.fun_no)
            if self._enum(function, switch.off_value, switch.on_value):
                entities.append(
                    HAEntitySpec(
                        device.c_dev_id,
                        "switch",
                        switch.mapping_key,
                        switch.name,
                        (switch.fun_no,),
                        ("turn_on", "turn_off") if function and function.writable else (),
                    )
                )
        return tuple(entities)

    def map_state(self, device: MLDevice, states: Mapping[str, str]) -> tuple[HAEntityState, ...]:
        functions = {function.fun_no: function for function in device.functions}
        result: list[HAEntityState] = []
        for entity in self.map_entities(device):
            fun_no = entity.consumed_fun_nos[0]
            value = states.get(fun_no)
            if entity.platform == "binary_sensor":
                result.append(self._boolean_state(entity.mapping_key, value))
            elif entity.platform == "sensor":
                metric = next(item for item in self._definition.numeric if item.fun_no == fun_no)
                result.append(self._numeric_state(entity.mapping_key, value, functions[fun_no], metric))
            else:
                switch = next(item for item in self._definition.switches if item.fun_no == fun_no)
                result.append(self._switch_state(entity.mapping_key, value, switch))
        return tuple(result)

    def map_command(self, device: MLDevice, entity: HAEntitySpec, command: HACommand) -> MLCommands:
        if entity not in self.map_entities(device) or entity.platform != "switch":
            raise UnsupportedHACommandError("sensor entities do not support control")
        if command.name not in entity.commands:
            raise UnsupportedHACommandError(f"unsupported sensor command {command.name!r}")
        switch = next(
            item for item in self._definition.switches if item.mapping_key == entity.mapping_key
        )
        value = switch.on_value if command.name == "turn_on" else switch.off_value
        return (MLControlCommand(device.c_dev_id, switch.fun_no, value),)

    @staticmethod
    def _boolean(function: MLFunction | None) -> bool:
        """Known binary points are useful whenever the gateway exposes them.

        appwsserver variants use either ``true``/``false`` or ``1``/``0``
        and some omit a useful range altogether.  The known type and function
        name establish the binary meaning; an unrecognised runtime value still
        leaves only that entity unavailable.
        """
        return bool(function and function.readable)

    @staticmethod
    def _enum(function: MLFunction | None, *values: str) -> bool:
        return bool(
            function
            and function.readable
            and (function.range_values is None or set(values).issubset(function.range_values))
        )

    @staticmethod
    def _limits(function: MLFunction, metric: _NumericDefinition) -> tuple[float, float] | None:
        try:
            minimum = float(function.minimum) if function.minimum else metric.minimum
            maximum = float(function.maximum) if function.maximum else metric.maximum
        except ValueError:
            return None
        if not all(math.isfinite(value) for value in (minimum, maximum)):
            return None
        minimum = max(minimum, metric.minimum)
        maximum = min(maximum, metric.maximum)
        return (minimum, maximum) if minimum <= maximum else None

    @staticmethod
    def _invalid_state(mapping_key: str, value: str | None) -> HAEntityState:
        attributes = {} if value is None else {"invalid_ml_value": value}
        return HAEntityState(mapping_key, None, False, attributes)

    def _boolean_state(self, mapping_key: str, value: str | None) -> HAEntityState:
        if value not in _BOOLEAN_TRUE_VALUES | _BOOLEAN_FALSE_VALUES:
            return self._invalid_state(mapping_key, value)
        return HAEntityState(mapping_key, value in _BOOLEAN_TRUE_VALUES, True)

    def _numeric_state(self, mapping_key: str, value: str | None, function: MLFunction, metric: _NumericDefinition) -> HAEntityState:
        limits = self._limits(function, metric)
        if value is None or limits is None:
            return self._invalid_state(mapping_key, value)
        try:
            parsed = float(value)
        except ValueError:
            return self._invalid_state(mapping_key, value)
        if not math.isfinite(parsed) or not limits[0] <= parsed <= limits[1]:
            return self._invalid_state(mapping_key, value)
        return HAEntityState(mapping_key, parsed, True)

    def _switch_state(self, mapping_key: str, value: str | None, definition: _SwitchDefinition) -> HAEntityState:
        if value not in {definition.off_value, definition.on_value}:
            return self._invalid_state(mapping_key, value)
        return HAEntityState(mapping_key, value == definition.on_value, True)
