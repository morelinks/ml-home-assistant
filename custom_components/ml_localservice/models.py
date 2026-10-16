"""Validated, Home-Assistant-independent models for appwsserver messages."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass


class MLModelValidationError(ValueError):
    """An appwsserver payload does not satisfy the supported contract."""


def _fail(path: str, problem: str) -> MLModelValidationError:
    return MLModelValidationError(f"{path}: {problem}")


def _object(value: object, path: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise _fail(path, "must be an object with string keys")
    return value


def _array(value: object, path: str, maximum: int) -> Sequence[object]:
    if not isinstance(value, list):
        raise _fail(path, "must be an array")
    if len(value) > maximum:
        raise _fail(path, f"must contain at most {maximum} items")
    return value


def _string(
    value: object,
    path: str,
    *,
    maximum: int,
    allow_empty: bool = False,
) -> str:
    if not isinstance(value, str) or (not value and not allow_empty):
        qualifier = "a string" if allow_empty else "a non-empty string"
        raise _fail(path, f"must be {qualifier}")
    if len(value.encode("utf-8")) > maximum:
        raise _fail(path, f"must be at most {maximum} UTF-8 bytes")
    return value


def _field(
    payload: Mapping[str, object],
    key: str,
    path: str,
    *,
    maximum: int,
    allow_empty: bool = False,
) -> str:
    if key not in payload:
        raise _fail(path, f"missing required field '{key}'")
    return _string(payload[key], f"{path}.{key}", maximum=maximum, allow_empty=allow_empty)


def _boolean(payload: Mapping[str, object], key: str, path: str) -> bool:
    value = payload.get(key)
    if not isinstance(value, bool):
        raise _fail(f"{path}.{key}", "must be a boolean")
    return value


def _command(payload: Mapping[str, object], expected: str, path: str) -> None:
    if _field(payload, "cmd", path, maximum=64) != expected:
        raise _fail(f"{path}.cmd", f"must be '{expected}'")


def _result(payload: Mapping[str, object], path: str) -> tuple[bool, str]:
    result = _field(payload, "result", path, maximum=16)
    if result not in {"success", "fail"}:
        raise _fail(f"{path}.result", "must be 'success' or 'fail'")
    return result == "success", _field(
        payload, "msg", path, maximum=1024, allow_empty=True
    )


@dataclass(frozen=True, slots=True)
class MLGatewayInfo:
    """Stable gateway identity and current appwsserver endpoint."""

    gateway_id: str
    host: str
    port: int = 8820

    def __post_init__(self) -> None:
        _string(self.gateway_id, "gateway.gateway_id", maximum=128)
        _string(self.host, "gateway.host", maximum=255)
        if self.port != 8820:
            raise _fail("gateway.port", "appwsserver port must be 8820")


@dataclass(frozen=True, slots=True)
class MLFunction:
    """One function advertised in a device-list snapshot."""

    fun_no: str
    fun_val: str
    readable: bool
    writable: bool
    minimum: str
    maximum: str
    range_values: tuple[str, ...] | None
    timestamp: str


@dataclass(frozen=True, slots=True)
class MLDevice:
    """One appwsserver device without HA platform or mapping decisions."""

    c_dev_id: str
    name: str
    main_type: str
    sub_type: str
    room_id: str
    functions: tuple[MLFunction, ...]


@dataclass(frozen=True, slots=True)
class MLDeviceState:
    """One flattened full-state value or real-time status notification."""

    c_dev_id: str
    fun_no: str
    fun_val: str
    timestamp: str


@dataclass(frozen=True, slots=True)
class MLControlCommand:
    """A validated control request, including optional remote-routing fields."""

    c_dev_id: str
    fun_no: str
    fun_val: str
    inner_fun_no: str | None = None
    open_device_id: str | None = None
    app_device_type_id: str | None = None
    # Local confirmation policy only; never included in the ML wire payload.
    confirmation_tolerance: int = 0

    def __post_init__(self) -> None:
        _string(self.c_dev_id, "control.c_dev_id", maximum=127)
        _string(self.fun_no, "control.fun_no", maximum=31)
        _string(self.fun_val, "control.fun_val", maximum=1023, allow_empty=True)
        if (
            isinstance(self.confirmation_tolerance, bool)
            or not isinstance(self.confirmation_tolerance, int)
            or self.confirmation_tolerance < 0
        ):
            raise _fail("control.confirmation_tolerance", "must be a non-negative integer")
        optional = (
            ("inner_fun_no", self.inner_fun_no, 31),
            ("open_device_id", self.open_device_id, 31),
            ("app_device_type_id", self.app_device_type_id, 15),
        )
        present = [value is not None for _, value, _ in optional]
        if any(present) and not all(present):
            raise _fail(
                "control",
                "inner_fun_no, open_device_id and app_device_type_id must be supplied together",
            )
        for name, value, maximum in optional:
            if value is not None:
                _string(value, f"control.{name}", maximum=maximum)

    def matches_state(self, value: str | None) -> bool:
        """Match exact states by default, or bounded integer telemetry error."""
        if self.confirmation_tolerance == 0:
            return value == self.fun_val
        if value is None:
            return False
        try:
            return abs(int(value) - int(self.fun_val)) <= self.confirmation_tolerance
        except ValueError:
            return False

    def to_payload(self) -> dict[str, str]:
        """Build the protocol fields without adding transport correlation data."""
        payload = {
            "cDevId": self.c_dev_id,
            "funNo": self.fun_no,
            "funVal": self.fun_val,
        }
        if self.inner_fun_no is not None:
            payload.update(
                {
                    "innerFunNo": self.inner_fun_no,
                    "openDeviceId": self.open_device_id or "",
                    "appDeviceTypeId": self.app_device_type_id or "",
                }
            )
        return payload


@dataclass(frozen=True, slots=True)
class MLControlResult:
    success: bool
    message: str
    request_id: str
    timestamp: str


@dataclass(frozen=True, slots=True)
class MLRegistrationResult:
    success: bool
    message: str


@dataclass(frozen=True, slots=True)
class MLRuntimeData:
    """Coordinator-facing immutable aggregate for later runtime work."""

    devices: tuple[MLDevice, ...]
    states: tuple[MLDeviceState, ...]


def parse_registration(value: object) -> MLRegistrationResult:
    payload = _object(value, "registerResult")
    _command(payload, "registerResult", "registerResult")
    success, message = _result(payload, "registerResult")
    return MLRegistrationResult(success, message)


def _parse_function(value: object, path: str) -> MLFunction:
    payload = _object(value, path)
    range_values: tuple[str, ...] | None = None
    if "range" in payload:
        raw_range = _array(payload["range"], f"{path}.range", 256)
        if any(not isinstance(item, str) for item in raw_range):
            raise _fail(f"{path}.range", "must contain only strings")
        range_values = tuple(raw_range)  # type: ignore[arg-type]
    return MLFunction(
        fun_no=_field(payload, "funNo", path, maximum=31),
        fun_val=_field(payload, "funVal", path, maximum=1023, allow_empty=True),
        readable=_boolean(payload, "r", path),
        writable=_boolean(payload, "w", path),
        minimum=_field(payload, "min", path, maximum=128, allow_empty=True),
        maximum=_field(payload, "max", path, maximum=128, allow_empty=True),
        range_values=range_values,
        timestamp=_field(payload, "ts", path, maximum=32, allow_empty=True),
    )


def parse_devices(
    value: object,
    *,
    max_devices: int = 4096,
    max_functions_per_device: int = 256,
) -> tuple[MLDevice, ...]:
    payload = _object(value, "syncDevListResult")
    _command(payload, "syncDevListResult", "syncDevListResult")
    success, _ = _result(payload, "syncDevListResult")
    if not success:
        return ()
    devices: list[MLDevice] = []
    identities: set[str] = set()
    for index, item in enumerate(
        _array(payload.get("devs"), "syncDevListResult.devs", max_devices)
    ):
        path = f"syncDevListResult.devs[{index}]"
        raw = _object(item, path)
        c_dev_id = _field(raw, "cDevId", path, maximum=127)
        if c_dev_id in identities:
            raise _fail("syncDevListResult.devs", "contains duplicate cDevId")
        raw_functions = _array(raw.get("funs"), f"{path}.funs", max_functions_per_device)
        functions = tuple(
            _parse_function(function, f"{path}.funs[{fun_index}]")
            for fun_index, function in enumerate(raw_functions)
        )
        fun_ids = [function.fun_no for function in functions]
        if len(fun_ids) != len(set(fun_ids)):
            raise _fail(f"{path}.funs", "contains duplicate funNo")
        identities.add(c_dev_id)
        devices.append(
            MLDevice(
                c_dev_id=c_dev_id,
                name=_field(raw, "name", path, maximum=255, allow_empty=True),
                main_type=_field(raw, "mainType", path, maximum=31),
                sub_type=_field(raw, "subType", path, maximum=31),
                room_id=_field(raw, "roomId", path, maximum=127, allow_empty=True),
                functions=functions,
            )
        )
    return tuple(devices)


def parse_states(value: object, *, max_states: int = 16384) -> tuple[MLDeviceState, ...]:
    payload = _object(value, "syncDevStatusResult")
    _command(payload, "syncDevStatusResult", "syncDevStatusResult")
    success, _ = _result(payload, "syncDevStatusResult")
    if not success:
        return ()
    timestamp = _field(payload, "ts", "syncDevStatusResult", maximum=32)
    states: list[MLDeviceState] = []
    for dev_index, item in enumerate(
        _array(payload.get("devStatus"), "syncDevStatusResult.devStatus", max_states)
    ):
        path = f"syncDevStatusResult.devStatus[{dev_index}]"
        raw = _object(item, path)
        c_dev_id = _field(raw, "cDevId", path, maximum=127)
        remaining = max_states - len(states)
        for state_index, status in enumerate(
            _array(raw.get("status"), f"{path}.status", remaining)
        ):
            status_path = f"{path}.status[{state_index}]"
            raw_status = _object(status, status_path)
            states.append(
                MLDeviceState(
                    c_dev_id=c_dev_id,
                    fun_no=_field(raw_status, "funNo", status_path, maximum=31),
                    fun_val=_field(
                        raw_status, "funVal", status_path, maximum=1023, allow_empty=True
                    ),
                    timestamp=timestamp,
                )
            )
    return tuple(states)


def parse_notification(value: object) -> MLDeviceState:
    payload = _object(value, "devStatusNotify")
    _command(payload, "devStatusNotify", "devStatusNotify")
    return MLDeviceState(
        c_dev_id=_field(payload, "cDevId", "devStatusNotify", maximum=127),
        fun_no=_field(payload, "funNo", "devStatusNotify", maximum=31),
        fun_val=_field(
            payload, "funVal", "devStatusNotify", maximum=1023, allow_empty=True
        ),
        timestamp=_field(payload, "ts", "devStatusNotify", maximum=32),
    )


def parse_control_result(value: object) -> MLControlResult:
    payload = _object(value, "ctrlDevResult")
    _command(payload, "ctrlDevResult", "ctrlDevResult")
    success, message = _result(payload, "ctrlDevResult")
    return MLControlResult(
        success=success,
        message=message,
        request_id=_field(payload, "reqId", "ctrlDevResult", maximum=64),
        timestamp=_field(payload, "ts", "ctrlDevResult", maximum=32),
    )
