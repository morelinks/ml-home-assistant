"""Category-based mappings for ML lighting devices."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from ..models import MLControlCommand, MLDevice, MLFunction
from .base import UnsupportedHACommandError, ml_device_spec
from .models import HACommand, HADeviceSpec, HAEntitySpec, HAEntityState, MLCommands

_TRUE = "true"
_FALSE = "false"
_POWER = "switch"
_BRIGHTNESS = "bright"
_COLOR_TEMP = "temp"
_RGB = "rgb"
_TWINKLE = "twinkle"
DEFAULT_COLOR_TEMP_LIMITS = (2000, 6535)


def color_temp_limits(device: MLDevice | None) -> tuple[int, int]:
    """Return ML's advertised Kelvin limits for both HA and command mapping."""
    function = next(
        (item for item in device.functions if item.fun_no == _COLOR_TEMP), None
    ) if device is not None else None
    if function is None:
        return DEFAULT_COLOR_TEMP_LIMITS
    minimum, maximum = LightMapper._limits(function, *DEFAULT_COLOR_TEMP_LIMITS)
    if minimum <= 0:
        raise ValueError("color temperature limits must be positive")
    return minimum, maximum


@dataclass(frozen=True, slots=True)
class _Capability:
    fun_no: str
    function: MLFunction


class LightMapper:
    """Map one documented ML lighting category to a single HA light.

    ML advertises the actual function schema with every device.  The category
    declares the *possible* functions, while this mapper enables each optional
    HA capability only when its function is present and readable/writable.
    """

    def __init__(
        self,
        *,
        brightness: bool = False,
        color_temp: bool = False,
        rgb: bool = False,
        twinkle: bool = False,
    ) -> None:
        self._brightness = brightness
        self._color_temp = color_temp
        self._rgb = rgb
        self._twinkle = twinkle

    def map_device(self, device: MLDevice) -> HADeviceSpec:
        return ml_device_spec(device, "light")

    def map_entities(self, device: MLDevice) -> tuple[HAEntitySpec, ...]:
        power = self._boolean(device, _POWER)
        if power is None or not power.readable:
            return ()
        consumed = [_POWER]
        commands: list[str] = []
        if power.writable:
            commands.extend(("turn_on", "turn_off"))
        for enabled, fun_no, command in (
            (self._brightness, _BRIGHTNESS, "set_brightness"),
            (self._color_temp, _COLOR_TEMP, "set_color_temp"),
            (self._rgb, _RGB, "set_rgb_color"),
            (self._twinkle, _TWINKLE, "flash"),
        ):
            function = self._function(device, fun_no)
            if enabled and function is not None:
                consumed.append(fun_no)
                if function.writable:
                    commands.append(command)
        return (
            HAEntitySpec(
                c_dev_id=device.c_dev_id,
                platform="light",
                # Keep the Task 1 switching-light identity stable while adding
                # capabilities to the same physical light projection.
                mapping_key="power",
                name=None,
                consumed_fun_nos=tuple(consumed),
                commands=tuple(commands),
            ),
        )

    def map_state(
        self, device: MLDevice, states: Mapping[str, str]
    ) -> tuple[HAEntityState, ...]:
        if not self.map_entities(device):
            return ()
        power = states.get(_POWER)
        if power not in {_TRUE, _FALSE}:
            return self._invalid_state(power)

        attributes: dict[str, object] = {}
        for enabled, fun_no, name, parser in (
            (self._brightness, _BRIGHTNESS, "brightness", self._parse_brightness),
            (self._color_temp, _COLOR_TEMP, "color_temp_kelvin", self._parse_int),
            (self._rgb, _RGB, "rgb_color", self._parse_rgb),
        ):
            function = self._function(device, fun_no)
            if not enabled or function is None or not function.readable:
                continue
            value = states.get(fun_no)
            if value is None:
                # Some appwsserver installations advertise an optional light
                # capability but omit it from full snapshots. The valid power
                # state remains sufficient to keep the light available.
                continue
            try:
                attributes[name] = parser(value, function)
            except ValueError:
                # Optional telemetry must not take an otherwise controllable
                # light offline when the gateway supplies an invalid value.
                continue
        return (HAEntityState("power", power == _TRUE, True, attributes),)

    def map_command(
        self, device: MLDevice, entity: HAEntitySpec, command: HACommand
    ) -> MLCommands:
        expected = self.map_entities(device)
        if not expected or entity != expected[0]:
            raise UnsupportedHACommandError(
                f"entity is not the light mapping for {device.c_dev_id}"
            )
        if command.name not in entity.commands:
            raise UnsupportedHACommandError(
                f"command {command.name!r} is unsupported by {entity.mapping_key}"
            )
        if command.name == "turn_on":
            return (MLControlCommand(device.c_dev_id, _POWER, _TRUE),)
        if command.name == "turn_off":
            return (MLControlCommand(device.c_dev_id, _POWER, _FALSE),)
        if command.name == "set_brightness":
            value = self._brightness_command(device, command.value)
            return (MLControlCommand(device.c_dev_id, _BRIGHTNESS, value),)
        if command.name == "set_color_temp":
            value = self._integer_command(device, _COLOR_TEMP, command.value)
            return (
                MLControlCommand(
                    device.c_dev_id, _COLOR_TEMP, value, confirmation_tolerance=100
                ),
            )
        if command.name == "set_rgb_color":
            value = self._rgb_command(command.value)
            return (MLControlCommand(device.c_dev_id, _RGB, value),)
        if command.name == "flash":
            value = self._flash_command(command.value)
            return (MLControlCommand(device.c_dev_id, _TWINKLE, value),)
        raise UnsupportedHACommandError(f"unsupported light command {command.name!r}")

    @staticmethod
    def _function(device: MLDevice, fun_no: str) -> MLFunction | None:
        return next((item for item in device.functions if item.fun_no == fun_no), None)

    def _boolean(self, device: MLDevice, fun_no: str) -> MLFunction | None:
        function = self._function(device, fun_no)
        if function is None:
            return None
        allowed = function.range_values
        if allowed is not None and not {_TRUE, _FALSE}.issubset(allowed):
            return None
        return function

    @staticmethod
    def _invalid_state(value: object | None) -> tuple[HAEntityState, ...]:
        attributes = {} if value is None else {"invalid_ml_value": value}
        return (HAEntityState("power", None, False, attributes),)

    @staticmethod
    def _limits(function: MLFunction, default_min: int, default_max: int) -> tuple[int, int]:
        try:
            minimum = int(function.minimum) if function.minimum else default_min
            maximum = int(function.maximum) if function.maximum else default_max
        except ValueError as err:
            raise ValueError("non-integer ML function limits") from err
        if minimum > maximum:
            raise ValueError("ML function minimum exceeds maximum")
        return minimum, maximum

    def _parse_brightness(self, value: str | None, function: MLFunction) -> int:
        if value is None:
            raise ValueError("missing brightness")
        minimum, maximum = self._limits(function, 0, 100)
        try:
            ml_value = int(value)
        except ValueError as err:
            raise ValueError("invalid brightness") from err
        if not minimum <= ml_value <= maximum:
            raise ValueError("brightness outside ML limits")
        if minimum == maximum:
            return 255
        return round((ml_value - minimum) * 255 / (maximum - minimum))

    def _parse_int(self, value: str | None, function: MLFunction) -> int:
        if value is None:
            raise ValueError("missing integer value")
        minimum, maximum = self._limits(function, *DEFAULT_COLOR_TEMP_LIMITS)
        try:
            parsed = int(value)
        except ValueError as err:
            raise ValueError("invalid integer value") from err
        if not minimum <= parsed <= maximum:
            raise ValueError("integer value outside ML limits")
        return parsed

    @staticmethod
    def _parse_rgb(value: str | None, _function: MLFunction) -> tuple[int, int, int]:
        if value is None:
            raise ValueError("missing RGB value")
        raw = value.removeprefix("#")
        if len(raw) != 6:
            raise ValueError("RGB value must contain six hexadecimal digits")
        try:
            return tuple(int(raw[index : index + 2], 16) for index in range(0, 6, 2))  # type: ignore[return-value]
        except ValueError as err:
            raise ValueError("invalid RGB value") from err

    def _brightness_command(self, device: MLDevice, value: object | None) -> str:
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 255:
            raise UnsupportedHACommandError("brightness must be an integer from 1 to 255")
        function = self._function(device, _BRIGHTNESS)
        if function is None:
            raise UnsupportedHACommandError("brightness is not supported")
        minimum, maximum = self._limits(function, 0, 100)
        return str(round(minimum + value * (maximum - minimum) / 255))

    def _integer_command(
        self, device: MLDevice, fun_no: str, value: object | None
    ) -> str:
        if isinstance(value, bool) or not isinstance(value, int):
            raise UnsupportedHACommandError("color temperature must be an integer")
        function = self._function(device, fun_no)
        if function is None:
            raise UnsupportedHACommandError("color temperature is not supported")
        minimum, maximum = color_temp_limits(device)
        return str(min(maximum, max(minimum, value)))

    @staticmethod
    def _rgb_command(value: object | None) -> str:
        if (
            not isinstance(value, tuple)
            or len(value) != 3
            or any(isinstance(item, bool) or not isinstance(item, int) or not 0 <= item <= 255 for item in value)
        ):
            raise UnsupportedHACommandError("RGB color must be a three-item 0-255 tuple")
        return "#%02X%02X%02X" % value

    @staticmethod
    def _flash_command(value: object | None) -> str:
        """Translate HA's standard flash duration into ML's trigger point."""
        if value not in {"short", "long"}:
            raise UnsupportedHACommandError("flash must be 'short' or 'long'")
        return _TRUE
