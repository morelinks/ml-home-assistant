"""Category-based mappings for documented ML curtain devices."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass

from ..models import MLControlCommand, MLDevice, MLFunction
from .base import UnsupportedHACommandError, ml_device_spec
from .models import HACommand, HADeviceSpec, HAEntitySpec, HAEntityState, MLCommands

_STOP = "0"
_OPEN = "1"
_CLOSE = "2"
_STATUS_VALUES = {_STOP, _OPEN, _CLOSE}
_JOG_VALUES = {_STOP, _OPEN}


@dataclass(frozen=True, slots=True)
class _CoverDefinition:
    """The documented points that form one HA cover projection."""

    mapping_key: str
    name: str | None
    status_fun_no: str
    position_fun_no: str | None = None
    tilt_fun_no: str | None = None
    tilt_maximum: float = 100.0
    rotation_fun_no: str | None = None


_NOTE_STANDARD_COVER = _CoverDefinition("cover", None, "status", "position")
_NOTE_DAY_COVER = _CoverDefinition(
    "day_curtain", "Day curtain", "day_status", "day_position"
)
_NOTE_NIGHT_COVER = _CoverDefinition(
    "night_curtain", "Night curtain", "night_status", "night_position"
)
_EPID_STANDARD_COVER = _CoverDefinition("cover", None, "5", "2")
_NOTE_TILT_BY_TYPE = {
    "11200002": ("angle", 180.0),
    "11200007": ("angle", 180.0),
    # This device advertises both an angle and an explicit angle percentage.
    # Prefer the latter so the HA value does not lose precision through scaling.
    "11200008": ("angle_percent", 100.0),
}
_ROTATION_TYPES = {"11200007", "11200008"}
_JOG_TYPES = {"11200005", "11200007"}


class CurtainMapper:
    """Map documented curtains to HA covers and optional jog buttons.

    Ordinary curtain devices expose their documented ``note`` values as
    appwsserver ``funNo`` strings. Curtain panels are different: their
    documentation has no notes, so their numeric epids remain the wire IDs.
    Position and tilt points are optional capabilities, rather than assumed
    from a category. Day/night curtains are one ML device with two independent
    mechanisms, so they deliberately emit two HA cover entities.
    """

    def __init__(
        self,
        classification: str = "curtain",
        *,
        numeric_epids: bool = False,
        inverted_position_device_ids: frozenset[str] = frozenset(),
    ) -> None:
        self._classification = classification
        self._numeric_epids = numeric_epids
        self._inverted_position_device_ids = inverted_position_device_ids

    @property
    def _jog_fun_no(self) -> str:
        return "8" if self._numeric_epids else "jog"

    def map_device(self, device: MLDevice) -> HADeviceSpec:
        return ml_device_spec(device, self._classification)

    def map_entities(self, device: MLDevice) -> tuple[HAEntitySpec, ...]:
        functions = {function.fun_no: function for function in device.functions}
        entities: list[HAEntitySpec] = []
        for definition in self._definitions(device):
            entity = self._cover_entity(device, functions, definition)
            if entity is not None:
                entities.append(entity)
        if device.sub_type in _JOG_TYPES:
            jog = self._jog_enum(functions.get(self._jog_fun_no))
            if jog is not None and jog.writable:
                entities.extend(
                    (
                        HAEntitySpec(
                            device.c_dev_id,
                            "button",
                            "jog_open",
                            "Jog open",
                            (self._jog_fun_no,),
                            ("press",),
                        ),
                        HAEntitySpec(
                            device.c_dev_id,
                            "button",
                            "jog_close",
                            "Jog close",
                            (self._jog_fun_no,),
                            ("press",),
                        ),
                    )
                )
        return tuple(entities)

    def map_state(
        self, device: MLDevice, states: Mapping[str, str]
    ) -> tuple[HAEntityState, ...]:
        functions = {function.fun_no: function for function in device.functions}
        result: list[HAEntityState] = []
        for entity in self.map_entities(device):
            definition = next(
                (
                    item
                    for item in self._definitions(device)
                    if item.mapping_key == entity.mapping_key
                ),
                None,
            )
            if definition is None:
                # Jog points are actions, not stateful curtain position.
                result.append(HAEntityState(entity.mapping_key, None, True))
            else:
                result.append(
                    self._cover_state(
                        functions, states, definition,
                        invert_position=(
                            device.c_dev_id in self._inverted_position_device_ids
                        ),
                    )
                )
        return tuple(result)

    def map_command(
        self, device: MLDevice, entity: HAEntitySpec, command: HACommand
    ) -> MLCommands:
        expected = self.map_entities(device)
        if entity not in expected or command.name not in entity.commands:
            raise UnsupportedHACommandError(
                f"unsupported curtain command {command.name!r}"
            )
        if entity.mapping_key == "jog_open":
            return (MLControlCommand(device.c_dev_id, self._jog_fun_no, _STOP),)
        if entity.mapping_key == "jog_close":
            return (MLControlCommand(device.c_dev_id, self._jog_fun_no, _OPEN),)

        definition = next(
            item
            for item in self._definitions(device)
            if item.mapping_key == entity.mapping_key
        )
        functions = {function.fun_no: function for function in device.functions}
        if command.name in {"open_cover", "close_cover", "stop_cover"}:
            value = {
                "open_cover": _OPEN,
                "close_cover": _CLOSE,
                "stop_cover": _STOP,
            }[command.name]
            return (MLControlCommand(device.c_dev_id, definition.status_fun_no, value),)
        if command.name == "set_cover_position":
            if definition.position_fun_no is None:
                raise UnsupportedHACommandError("curtain position is not supported")
            function = functions[definition.position_fun_no]
            # Validate in HA coordinates before converting; advertised limits
            # describe the ML wire value, so apply them after conversion.
            percentage = float(self._percentage_command(command.value, None, 100.0))
            if device.c_dev_id in self._inverted_position_device_ids:
                percentage = 100.0 - percentage
            return (
                MLControlCommand(
                    device.c_dev_id,
                    definition.position_fun_no,
                    self._percentage_command(percentage, function, 100.0),
                ),
            )
        if command.name in {
            "open_cover_tilt",
            "close_cover_tilt",
            "stop_cover_tilt",
        }:
            if definition.rotation_fun_no is None:
                raise UnsupportedHACommandError("curtain tilt rotation is not supported")
            value = {
                "open_cover_tilt": _OPEN,
                "close_cover_tilt": _CLOSE,
                "stop_cover_tilt": _STOP,
            }[command.name]
            return (MLControlCommand(device.c_dev_id, definition.rotation_fun_no, value),)
        if command.name == "set_cover_tilt_position":
            if definition.tilt_fun_no is None:
                raise UnsupportedHACommandError("curtain tilt position is not supported")
            function = functions[definition.tilt_fun_no]
            percentage = self._percentage_command(command.value, None, 100.0)
            raw = float(percentage) * definition.tilt_maximum / 100.0
            raw_value = self._format_number(raw)
            if not self._within_limits(raw, function, definition.tilt_maximum):
                raise UnsupportedHACommandError("curtain tilt is outside the advertised range")
            return (
                MLControlCommand(device.c_dev_id, definition.tilt_fun_no, raw_value),
            )
        raise UnsupportedHACommandError(
            f"unsupported curtain command {command.name!r}"
        )

    def _cover_entity(
        self,
        device: MLDevice,
        functions: Mapping[str, MLFunction],
        definition: _CoverDefinition,
    ) -> HAEntitySpec | None:
        status = self._enum(functions.get(definition.status_fun_no))
        position = self._number(
            functions.get(definition.position_fun_no)
            if definition.position_fun_no is not None
            else None,
            100.0,
        )
        tilt = self._number(
            functions.get(definition.tilt_fun_no)
            if definition.tilt_fun_no is not None
            else None,
            definition.tilt_maximum,
        )
        rotation = self._enum(
            functions.get(definition.rotation_fun_no)
            if definition.rotation_fun_no is not None
            else None,
        )
        consumed: list[str] = []
        commands: list[str] = []
        if status is not None and status.readable:
            consumed.append(definition.status_fun_no)
            if status.writable:
                commands.extend(("open_cover", "close_cover", "stop_cover"))
        if position is not None and position.readable and definition.position_fun_no is not None:
            consumed.append(definition.position_fun_no)
            if position.writable:
                commands.append("set_cover_position")
        if tilt is not None and tilt.readable and definition.tilt_fun_no is not None:
            consumed.append(definition.tilt_fun_no)
            if tilt.writable:
                commands.append("set_cover_tilt_position")
        if rotation is not None and rotation.readable and definition.rotation_fun_no is not None:
            consumed.append(definition.rotation_fun_no)
            if rotation.writable:
                commands.extend(
                    ("open_cover_tilt", "close_cover_tilt", "stop_cover_tilt")
                )
        if not consumed:
            return None
        return HAEntitySpec(
            device.c_dev_id,
            "cover",
            definition.mapping_key,
            definition.name,
            tuple(consumed),
            tuple(commands),
        )

    def _cover_state(
        self,
        functions: Mapping[str, MLFunction],
        states: Mapping[str, str],
        definition: _CoverDefinition,
        *,
        invert_position: bool = False,
    ) -> HAEntityState:
        attributes: dict[str, object] = {}
        valid = False
        invalid_value: str | None = None

        status = self._enum(functions.get(definition.status_fun_no))
        status_value = states.get(definition.status_fun_no)
        if status is not None and status.readable:
            if status_value in _STATUS_VALUES:
                valid = True
                if status_value == _OPEN:
                    attributes["is_opening"] = True
                    attributes["is_closed"] = False
                elif status_value == _CLOSE:
                    attributes["is_closing"] = True
            elif status_value is not None:
                invalid_value = status_value

        position_value = self._state_percentage(
            functions.get(definition.position_fun_no)
            if definition.position_fun_no is not None
            else None,
            states.get(definition.position_fun_no)
            if definition.position_fun_no is not None
            else None,
            100.0,
        )
        if position_value is not None:
            if invert_position:
                position_value = 100 - position_value
            valid = True
            attributes["current_cover_position"] = position_value
            attributes["is_closed"] = position_value == 0
        elif definition.position_fun_no is not None and states.get(definition.position_fun_no) is not None:
            invalid_value = invalid_value or states[definition.position_fun_no]

        tilt_value = self._state_percentage(
            functions.get(definition.tilt_fun_no)
            if definition.tilt_fun_no is not None
            else None,
            states.get(definition.tilt_fun_no)
            if definition.tilt_fun_no is not None
            else None,
            definition.tilt_maximum,
        )
        if tilt_value is not None:
            valid = True
            attributes["current_cover_tilt_position"] = tilt_value
        elif definition.tilt_fun_no is not None and states.get(definition.tilt_fun_no) is not None:
            invalid_value = invalid_value or states[definition.tilt_fun_no]

        rotation = self._enum(
            functions.get(definition.rotation_fun_no)
            if definition.rotation_fun_no is not None
            else None,
        )
        rotation_value = (
            states.get(definition.rotation_fun_no)
            if definition.rotation_fun_no is not None
            else None
        )
        if rotation is not None and rotation.readable:
            if rotation_value in _STATUS_VALUES:
                valid = True
            elif rotation_value is not None:
                invalid_value = invalid_value or rotation_value

        if valid:
            return HAEntityState(definition.mapping_key, status_value, True, attributes)
        diagnostic = {} if invalid_value is None else {"invalid_ml_value": invalid_value}
        return HAEntityState(definition.mapping_key, None, False, diagnostic)

    def _definitions(self, device: MLDevice) -> tuple[_CoverDefinition, ...]:
        if self._numeric_epids:
            return (_EPID_STANDARD_COVER,)
        if device.sub_type == "11200009":
            return (_NOTE_DAY_COVER, _NOTE_NIGHT_COVER)
        tilt = _NOTE_TILT_BY_TYPE.get(device.sub_type)
        return (
            _CoverDefinition(
                "cover",
                None,
                "status",
                "position",
                tilt_fun_no=tilt[0] if tilt is not None else None,
                tilt_maximum=tilt[1] if tilt is not None else 100.0,
                rotation_fun_no=(
                    "control_back_mode" if device.sub_type in _ROTATION_TYPES else None
                ),
            ),
        )

    @staticmethod
    def _enum(function: MLFunction | None) -> MLFunction | None:
        if function is None:
            return None
        if function.range_values is not None and not _STATUS_VALUES.issubset(
            function.range_values
        ):
            return None
        return function

    @staticmethod
    def _jog_enum(function: MLFunction | None) -> MLFunction | None:
        if function is None:
            return None
        if function.range_values is not None and not _JOG_VALUES.issubset(
            function.range_values
        ):
            return None
        return function

    def _number(self, function: MLFunction | None, maximum: float) -> MLFunction | None:
        if function is None or self._limits(function, maximum) is None:
            return None
        return function

    def _state_percentage(
        self, function: MLFunction | None, value: str | None, maximum: float
    ) -> int | None:
        if function is None or not function.readable or value is None:
            return None
        try:
            raw = float(value)
        except ValueError:
            return None
        if not self._within_limits(raw, function, maximum):
            return None
        return round(raw * 100.0 / maximum)

    def _percentage_command(
        self, value: object | None, function: MLFunction | None, maximum: float
    ) -> str:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise UnsupportedHACommandError("curtain position must be numeric")
        number = float(value)
        if not math.isfinite(number) or not 0.0 <= number <= 100.0:
            raise UnsupportedHACommandError("curtain position must be between 0 and 100")
        raw = number * maximum / 100.0
        if function is not None and not self._within_limits(raw, function, maximum):
            raise UnsupportedHACommandError("curtain position is outside the advertised range")
        return self._format_number(raw)

    @staticmethod
    def _format_number(value: float) -> str:
        return str(int(value)) if value.is_integer() else str(value)

    @staticmethod
    def _limits(function: MLFunction, maximum: float) -> tuple[float, float] | None:
        try:
            lower = float(function.minimum) if function.minimum else 0.0
            upper = float(function.maximum) if function.maximum else maximum
        except ValueError:
            return None
        if not all(math.isfinite(value) for value in (lower, upper)):
            return None
        lower, upper = max(lower, 0.0), min(upper, maximum)
        return (lower, upper) if lower <= upper else None

    def _within_limits(
        self, value: float, function: MLFunction, maximum: float
    ) -> bool:
        limits = self._limits(function, maximum)
        return (
            limits is not None
            and math.isfinite(value)
            and limits[0] <= value <= limits[1]
        )
