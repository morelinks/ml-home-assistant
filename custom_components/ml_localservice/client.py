"""Reusable asynchronous client for the existing appwsserver protocol."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any

from .connection import (
    ConnectionTestError,
    _async_host_search_result,
    _registration_payload,
)
from .const import (
    APPLICATION_HEARTBEAT_INTERVAL,
    APPWSSERVER_PORT,
    CONNECTION_TIMEOUT,
    MAX_DEVICES,
    MAX_EVENT_QUEUE_SIZE,
    MAX_FUNCTIONS_PER_DEVICE,
    MAX_MESSAGE_BYTES,
    MAX_PENDING_REQUESTS,
    MAX_STATES,
    REQUEST_TIMEOUT,
    SYNC_INTERVAL,
    SYNC_REQUEST_TIMEOUT,
    SYNC_RETRIES,
    WEBSOCKET_HEARTBEAT,
)
from .models import (
    MLControlCommand,
    MLControlResult,
    MLDevice,
    MLDeviceState,
    MLModelValidationError,
    MLRegistrationResult,
    parse_control_result,
    parse_devices,
    parse_notification,
    parse_registration,
    parse_states,
)

_LOGGER = logging.getLogger(__name__)


class MLClientError(Exception):
    """Base class for classified appwsserver client failures."""


class MLConnectionError(MLClientError):
    """The gateway could not be reached or the WebSocket failed."""


class MLConnectionClosedError(MLConnectionError):
    """The established WebSocket closed or the client was closed."""


class MLTimeoutError(MLConnectionError):
    """A bounded connection, registration, request, or event wait expired."""


class MLProtocolError(MLClientError):
    """The gateway sent an invalid supported-protocol message."""


class MLAuthenticationError(MLClientError):
    """The gateway rejected registration."""


class MLGatewayIdentityMismatchError(MLConnectionError):
    """The endpoint belongs to a different ML Home than this Config Entry."""


class MLCapacityError(MLClientError):
    """A bounded pending-request or event resource was exhausted."""


class MLStateError(MLClientError):
    """An operation is invalid for the current client lifecycle state."""


class MLRequestRejectedError(MLClientError):
    """appwsserver returned a correlated failure response."""

    def __init__(self, command: str, message: str) -> None:
        self.command = command
        self.server_message = message
        super().__init__(f"{command} request was rejected: {message or 'unspecified error'}")


class MLClientState(Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    REGISTERING = "registering"
    REGISTERED = "registered"
    CLOSING = "closing"
    CLOSED = "closed"


@dataclass(slots=True)
class _PendingRequest:
    expected_command: str
    future: asyncio.Future[Mapping[str, object]]


@dataclass(frozen=True, slots=True)
class _EventStreamClosed:
    error: MLClientError


class MLAppWSClient:
    """One bounded appwsserver connection with a single WebSocket reader.

    The injected session is owned by Home Assistant.  This client closes only
    its WebSocket and never closes that shared session.
    """

    def __init__(
        self,
        session: Any,
        host: str,
        gateway_id: str,
        expected_home_fingerprint: str,
        *,
        port: int = APPWSSERVER_PORT,
        connect_timeout: float = CONNECTION_TIMEOUT,
        request_timeout: float = REQUEST_TIMEOUT,
        sync_timeout: float = SYNC_REQUEST_TIMEOUT,
        sync_interval: float = SYNC_INTERVAL,
        sync_retries: int = SYNC_RETRIES,
        heartbeat: float = WEBSOCKET_HEARTBEAT,
        application_heartbeat_interval: float = APPLICATION_HEARTBEAT_INTERVAL,
        max_message_bytes: int = MAX_MESSAGE_BYTES,
        max_pending_requests: int = MAX_PENDING_REQUESTS,
        event_queue_size: int = MAX_EVENT_QUEUE_SIZE,
        max_devices: int = MAX_DEVICES,
        max_functions_per_device: int = MAX_FUNCTIONS_PER_DEVICE,
        max_states: int = MAX_STATES,
        request_id_factory: Callable[[], str] | None = None,
        identity_mismatch_callback: Callable[[], None] | None = None,
    ) -> None:
        if not host or not gateway_id:
            raise ValueError("host and gateway_id are required")
        if (
            not isinstance(expected_home_fingerprint, str)
            or len(expected_home_fingerprint) != 32
            or any(character not in "0123456789abcdef" for character in expected_home_fingerprint)
        ):
            raise ValueError("expected_home_fingerprint must be a SHA-256 prefix")
        if port != APPWSSERVER_PORT:
            raise ValueError("appwsserver port must be 8820")
        if (
            connect_timeout <= 0
            or request_timeout <= 0
            or sync_timeout <= 0
            or heartbeat <= 0
            or application_heartbeat_interval <= 0
        ):
            raise ValueError("timeouts and heartbeat must be positive")
        if sync_interval < 0 or sync_retries < 0:
            raise ValueError("sync_interval and sync_retries cannot be negative")
        if max_message_bytes < 1 or max_pending_requests < 1 or event_queue_size < 1:
            raise ValueError("client bounds must be positive")
        if max_devices < 1 or max_functions_per_device < 1 or max_states < 1:
            raise ValueError("model bounds must be positive")

        self._session = session
        self._host = host
        self._gateway_id = gateway_id
        self._expected_home_fingerprint = expected_home_fingerprint
        self._port = port
        self._connect_timeout = connect_timeout
        self._request_timeout = request_timeout
        self._sync_timeout = sync_timeout
        self._sync_interval = sync_interval
        self._sync_retries = sync_retries
        self._heartbeat = heartbeat
        self._application_heartbeat_interval = application_heartbeat_interval
        self._max_message_bytes = max_message_bytes
        self._max_pending_requests = max_pending_requests
        self._max_devices = max_devices
        self._max_functions_per_device = max_functions_per_device
        self._max_states = max_states
        self._request_id_factory = request_id_factory or (lambda: uuid.uuid4().hex)
        self._identity_mismatch_callback = identity_mismatch_callback

        self._state = MLClientState.DISCONNECTED
        self._websocket: Any | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._application_heartbeat_task: asyncio.Task[None] | None = None
        self._registration_future: asyncio.Future[Mapping[str, object]] | None = None
        self._registration_result: MLRegistrationResult | None = None
        self._pending: dict[str, _PendingRequest] = {}
        self._events: asyncio.Queue[MLDeviceState | _EventStreamClosed] = asyncio.Queue(
            event_queue_size
        )
        self._terminal_error: MLClientError | None = None
        self._last_sync_sent: float | None = None

        self._write_lock = asyncio.Lock()
        self._sync_lock = asyncio.Lock()
        self._register_lock = asyncio.Lock()
        self._close_lock = asyncio.Lock()

    @property
    def state(self) -> MLClientState:
        return self._state

    @property
    def connected(self) -> bool:
        return self._state in {
            MLClientState.CONNECTED,
            MLClientState.REGISTERING,
            MLClientState.REGISTERED,
        }

    @property
    def registered(self) -> bool:
        return self._state is MLClientState.REGISTERED

    @property
    def pending_request_count(self) -> int:
        return len(self._pending)

    @property
    def reader_task(self) -> asyncio.Task[None] | None:
        """Expose lifecycle state for diagnostics and leak assertions."""
        return self._reader_task

    async def connect(self) -> None:
        """Open the fixed appwsserver WebSocket and start its sole reader."""
        if self._state is not MLClientState.DISCONNECTED:
            raise MLStateError("connect requires a new disconnected client")
        if bool(getattr(self._session, "closed", False)):
            raise MLConnectionError("the injected HTTP session is closed")

        self._state = MLClientState.CONNECTING
        host = (
            f"[{self._host}]"
            if ":" in self._host and not self._host.startswith("[")
            else self._host
        )
        url = f"ws://{host}:{self._port}"
        try:
            async with asyncio.timeout(self._connect_timeout):
                websocket = await self._session.ws_connect(
                    url,
                    heartbeat=self._heartbeat,
                    autoping=True,
                    autoclose=True,
                    max_msg_size=self._max_message_bytes,
                )
        except asyncio.CancelledError:
            self._state = MLClientState.CLOSED
            raise
        except TimeoutError:
            self._state = MLClientState.CLOSED
            raise MLTimeoutError("appwsserver connection timed out") from None
        # aiohttp is supplied by HA and intentionally imported nowhere here.
        except Exception as err:
            self._state = MLClientState.CLOSED
            status = getattr(err, "status", None)
            if status in {401, 403}:
                raise MLAuthenticationError("appwsserver WebSocket upgrade was rejected") from None
            raise MLConnectionError("cannot connect to appwsserver") from None

        if self._state is not MLClientState.CONNECTING:
            try:
                await websocket.close()
            except Exception:
                pass
            raise MLConnectionClosedError("client closed while connecting")

        self._websocket = websocket
        self._terminal_error = None
        self._state = MLClientState.CONNECTED
        self._reader_task = asyncio.create_task(
            self._reader_loop(websocket), name="ml_localservice_appwsserver_reader"
        )

    async def register(self) -> MLRegistrationResult:
        """Acquire a one-shot UDP token and complete registration."""
        async with self._register_lock:
            if self._state is MLClientState.REGISTERED and self._registration_result:
                return self._registration_result
            if self._state is not MLClientState.CONNECTED:
                raise MLStateError("register requires a connected client")

            dev_id = f"ha-{self._gateway_id}"
            try:
                token, home_fingerprint = await _async_host_search_result(
                    self._host, dev_id, self._connect_timeout
                )
            except asyncio.CancelledError:
                raise
            except (ConnectionTestError, OSError) as err:
                raise MLConnectionError("cannot obtain appwsserver registration token") from err
            if home_fingerprint != self._expected_home_fingerprint:
                await self.close()
                if self._identity_mismatch_callback is not None:
                    try:
                        self._identity_mismatch_callback()
                    except Exception:  # noqa: BLE001 - discovery must not bypass quarantine
                        _LOGGER.exception("Failed to trigger ML gateway rediscovery")
                raise MLGatewayIdentityMismatchError(
                    "appwsserver endpoint identity does not match the configured ML gateway"
                )
            if self._state is not MLClientState.CONNECTED:
                raise MLConnectionClosedError("client closed while acquiring registration token")

            loop = asyncio.get_running_loop()
            future: asyncio.Future[Mapping[str, object]] = loop.create_future()
            self._registration_future = future
            self._state = MLClientState.REGISTERING
            try:
                await self._send_json(_registration_payload(dev_id, token))
                async with asyncio.timeout(self._request_timeout):
                    payload = await asyncio.shield(future)
            except TimeoutError:
                if not future.done():
                    future.cancel()
                if self._state is MLClientState.REGISTERING:
                    self._state = MLClientState.CONNECTED
                raise MLTimeoutError("appwsserver registration timed out") from None
            except asyncio.CancelledError:
                if not future.done():
                    future.cancel()
                if self._state is MLClientState.REGISTERING:
                    self._state = MLClientState.CONNECTED
                raise
            except MLClientError:
                if not future.done():
                    future.cancel()
                if self._state is MLClientState.REGISTERING:
                    self._state = MLClientState.CONNECTED
                raise
            finally:
                if self._registration_future is future:
                    self._registration_future = None

            try:
                result = self._parse(parse_registration, payload)
            except MLProtocolError:
                await self.close()
                raise
            if not result.success:
                await self.close()
                raise MLAuthenticationError("appwsserver registration was rejected")
            if self._state is not MLClientState.REGISTERING:
                raise MLConnectionClosedError("client closed during registration")
            self._registration_result = result
            self._state = MLClientState.REGISTERED
            self._application_heartbeat_task = asyncio.create_task(
                self._application_heartbeat_loop(),
                name="ml_localservice_appwsserver_application_heartbeat",
            )
            return result

    async def sync_devices(self) -> tuple[MLDevice, ...]:
        """Return a fully validated device-list snapshot."""
        payload = await self._sync_request("syncDevList", "syncDevListResult")
        self._raise_if_rejected(payload, "syncDevList")
        return self._parse(
            lambda value: parse_devices(
                value,
                max_devices=self._max_devices,
                max_functions_per_device=self._max_functions_per_device,
            ),
            payload,
        )

    async def sync_states(self) -> tuple[MLDeviceState, ...]:
        """Return a fully validated and flattened full-state snapshot."""
        payload = await self._sync_request("syncDevStatus", "syncDevStatusResult")
        self._raise_if_rejected(payload, "syncDevStatus")
        return self._parse(
            lambda value: parse_states(value, max_states=self._max_states), payload
        )

    async def control(self, command: MLControlCommand) -> MLControlResult:
        """Submit a control without waiting behind the synchronization throttle."""
        if not isinstance(command, MLControlCommand):
            raise TypeError("command must be MLControlCommand")
        payload = await self._request(
            "ctrlDev", "ctrlDevResult", command.to_payload(), self._request_timeout
        )
        result = self._parse(parse_control_result, payload)
        if not result.success:
            raise MLRequestRejectedError("ctrlDev", result.message)
        return result

    async def next_event(self, timeout: float | None = None) -> MLDeviceState:
        """Wait for the next validated ``devStatusNotify`` model."""
        if self._events.empty() and self._terminal_error is not None:
            raise self._terminal_error
        try:
            if timeout is None:
                item = await self._events.get()
            else:
                if timeout <= 0:
                    raise ValueError("timeout must be positive")
                async with asyncio.timeout(timeout):
                    item = await self._events.get()
        except TimeoutError:
            raise MLTimeoutError("status event wait timed out") from None
        if isinstance(item, _EventStreamClosed):
            raise item.error
        return item

    async def close(self) -> None:
        """Idempotently close only this WebSocket and reap the reader task."""
        async with self._close_lock:
            if self._state is MLClientState.CLOSED:
                task = self._reader_task
                if task is None or task.done():
                    return
            self._state = MLClientState.CLOSING
            error = MLConnectionClosedError("appwsserver client was closed")
            self._fail_waiters(error)
            websocket = self._websocket
            task = self._reader_task
            heartbeat_task = self._application_heartbeat_task
            self._application_heartbeat_task = None
            if (
                heartbeat_task is not None
                and heartbeat_task is not asyncio.current_task()
                and not heartbeat_task.done()
            ):
                heartbeat_task.cancel()
                try:
                    await heartbeat_task
                except asyncio.CancelledError:
                    pass
            if websocket is not None:
                try:
                    await websocket.close()
                except Exception:
                    _LOGGER.debug("Failed to close appwsserver WebSocket", exc_info=True)
            if task is not None and task is not asyncio.current_task() and not task.done():
                try:
                    async with asyncio.timeout(2.0):
                        await asyncio.shield(task)
                except TimeoutError:
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
            self._state = MLClientState.CLOSED
            self._websocket = None

    async def _sync_request(
        self, command: str, expected_command: str
    ) -> Mapping[str, object]:
        async with self._sync_lock:
            for attempt in range(self._sync_retries + 1):
                if self._last_sync_sent is not None:
                    elapsed = asyncio.get_running_loop().time() - self._last_sync_sent
                    delay = self._sync_interval - elapsed
                    if delay > 0:
                        await asyncio.sleep(delay)
                self._last_sync_sent = asyncio.get_running_loop().time()
                try:
                    return await self._request(
                        command, expected_command, {}, self._sync_timeout
                    )
                except MLTimeoutError:
                    if attempt >= self._sync_retries:
                        raise
            raise AssertionError("unreachable sync retry state")

    async def _request(
        self,
        command: str,
        expected_command: str,
        fields: Mapping[str, str],
        timeout: float,
    ) -> Mapping[str, object]:
        if self._state is not MLClientState.REGISTERED:
            raise MLStateError("requests require a registered client")
        if len(self._pending) >= self._max_pending_requests:
            raise MLCapacityError("pending request limit reached")

        request_id = self._new_request_id()
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Mapping[str, object]] = loop.create_future()
        self._pending[request_id] = _PendingRequest(expected_command, future)
        payload = {"cmd": command, **fields, "reqId": request_id}
        try:
            await self._send_json(payload)
            async with asyncio.timeout(timeout):
                return await asyncio.shield(future)
        except TimeoutError:
            if not future.done():
                future.cancel()
            raise MLTimeoutError(f"{command} request timed out") from None
        except asyncio.CancelledError:
            if not future.done():
                future.cancel()
            raise
        finally:
            pending = self._pending.get(request_id)
            if pending is not None and pending.future is future:
                self._pending.pop(request_id, None)

    def _new_request_id(self) -> str:
        for _ in range(8):
            value = self._request_id_factory()
            if (
                isinstance(value, str)
                and value
                and len(value.encode("utf-8")) <= 64
                and value not in self._pending
            ):
                return value
        raise MLCapacityError("could not allocate a unique bounded reqId")

    async def _send_json(self, payload: Mapping[str, object]) -> None:
        websocket = self._websocket
        if websocket is None or self._state in {
            MLClientState.CLOSING,
            MLClientState.CLOSED,
        }:
            raise MLConnectionClosedError("appwsserver WebSocket is not open")
        try:
            async with self._write_lock:
                await websocket.send_json(dict(payload))
        except asyncio.CancelledError:
            raise
        except Exception as err:
            raise MLConnectionError("failed to send appwsserver message") from err

    async def _application_heartbeat_loop(self) -> None:
        """Keep appwsserver's application-level idle timer refreshed."""
        try:
            while self._state is MLClientState.REGISTERED:
                await asyncio.sleep(self._application_heartbeat_interval)
                if self._state is not MLClientState.REGISTERED:
                    return
                # appwsserver updates its client activity timestamp before it
                # dispatches a command. "ping" is intentionally unsupported
                # by the legacy protocol and therefore has no side effects.
                await self._send_json({"cmd": "ping"})
        except asyncio.CancelledError:
            raise
        except MLClientError:
            # The reader/coordinator owns connection recovery.
            return

    async def _reader_loop(self, websocket: Any) -> None:
        terminal: MLClientError = MLConnectionClosedError(
            "appwsserver WebSocket closed"
        )
        try:
            while True:
                message = await websocket.receive()
                kind = self._message_kind(message)
                if kind == "TEXT":
                    await self._handle_text(getattr(message, "data", None))
                elif kind == "PING":
                    await websocket.pong(getattr(message, "data", b""))
                elif kind == "PONG":
                    continue
                elif kind == "ERROR":
                    terminal = MLConnectionClosedError("appwsserver WebSocket failed")
                    break
                elif kind in {"CLOSE", "CLOSING", "CLOSED"}:
                    break
                else:
                    raise MLProtocolError("appwsserver sent a non-text data message")
        except asyncio.CancelledError:
            terminal = MLConnectionClosedError("appwsserver reader was cancelled")
        except MLConnectionClosedError as err:
            terminal = err
        except MLClientError as err:
            terminal = err
        except Exception:
            terminal = MLConnectionClosedError("appwsserver reader failed")
            _LOGGER.debug("Unexpected appwsserver reader failure", exc_info=True)
        finally:
            self._reader_ended(terminal)
            if not bool(getattr(websocket, "closed", False)):
                try:
                    await websocket.close()
                except Exception:
                    pass

    @staticmethod
    def _message_kind(message: object) -> str:
        raw = getattr(message, "type", None)
        name = getattr(raw, "name", None)
        if isinstance(name, str):
            return name
        if isinstance(raw, str):
            return raw.upper()
        numeric = {
            1: "TEXT",
            2: "BINARY",
            8: "CLOSE",
            9: "PING",
            10: "PONG",
            256: "CLOSING",
            257: "CLOSED",
            258: "ERROR",
        }
        return numeric.get(raw, "UNKNOWN")

    async def _handle_text(self, data: object) -> None:
        if not isinstance(data, str):
            raise MLProtocolError("text message payload is not a string")
        if len(data.encode("utf-8")) > self._max_message_bytes:
            raise MLCapacityError("appwsserver message size limit exceeded")
        try:
            decoded = json.loads(data)
        except json.JSONDecodeError as err:
            raise MLProtocolError("appwsserver sent invalid JSON") from err
        if not isinstance(decoded, Mapping) or any(
            not isinstance(key, str) for key in decoded
        ):
            raise MLProtocolError("appwsserver message must be an object")
        command = decoded.get("cmd")
        if not isinstance(command, str) or not command:
            raise MLProtocolError("appwsserver message has no valid cmd")

        if command == "registerResult":
            future = self._registration_future
            if future is not None and not future.done():
                future.set_result(decoded)
            return
        if command == "devStatusNotify":
            event = self._parse(parse_notification, decoded)
            try:
                self._events.put_nowait(event)
            except asyncio.QueueFull as err:
                raise MLCapacityError("status event queue limit reached") from err
            return
        if command not in {"syncDevListResult", "syncDevStatusResult", "ctrlDevResult"}:
            _LOGGER.debug("Ignoring unsupported appwsserver command %s", command)
            return

        request_id = decoded.get("reqId")
        if not isinstance(request_id, str) or not request_id:
            raise MLProtocolError(f"{command} has no valid reqId")
        pending = self._pending.get(request_id)
        if pending is None or pending.future.done():
            _LOGGER.debug("Ignoring late or unknown appwsserver response %s", command)
            return
        if pending.expected_command != command:
            pending.future.set_exception(
                MLProtocolError(
                    f"expected {pending.expected_command} for reqId but received {command}"
                )
            )
            return
        pending.future.set_result(decoded)

    def _reader_ended(self, error: MLClientError) -> None:
        heartbeat_task = self._application_heartbeat_task
        self._application_heartbeat_task = None
        if heartbeat_task is not None and heartbeat_task is not asyncio.current_task():
            heartbeat_task.cancel()
        self._terminal_error = error
        self._fail_waiters(error)
        self._registration_result = None
        self._websocket = None
        if self._state is not MLClientState.CLOSING:
            self._state = MLClientState.CLOSED
        marker = _EventStreamClosed(error)
        try:
            self._events.put_nowait(marker)
        except asyncio.QueueFull:
            try:
                self._events.get_nowait()
            except asyncio.QueueEmpty:
                pass
            self._events.put_nowait(marker)

    def _fail_waiters(self, error: MLClientError) -> None:
        future = self._registration_future
        if future is not None and not future.done():
            future.set_exception(error)
        for pending in tuple(self._pending.values()):
            if not pending.future.done():
                pending.future.set_exception(error)
        self._pending.clear()

    @staticmethod
    def _raise_if_rejected(payload: Mapping[str, object], command: str) -> None:
        result = payload.get("result")
        message = payload.get("msg")
        if result == "fail" and isinstance(message, str):
            raise MLRequestRejectedError(command, message)
        if result != "success" or not isinstance(message, str):
            raise MLProtocolError(f"{command} response has an invalid result")

    @staticmethod
    def _parse(parser: Callable[[object], Any], value: object) -> Any:
        try:
            return parser(value)
        except MLModelValidationError as err:
            raise MLProtocolError(str(err)) from err


__all__ = [
    "MLAppWSClient",
    "MLAuthenticationError",
    "MLCapacityError",
    "MLClientError",
    "MLClientState",
    "MLConnectionClosedError",
    "MLConnectionError",
    "MLProtocolError",
    "MLRequestRejectedError",
    "MLStateError",
    "MLTimeoutError",
]
