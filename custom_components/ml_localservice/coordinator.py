"""Per-config-entry runtime coordinator for ML LocalService gateways."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from types import MappingProxyType
from typing import Protocol

from .client import MLClientError, MLConnectionError, MLTimeoutError
from .const import (
    CONTROL_CONFIRMATION_TIMEOUT,
    DEVICE_REFRESH_INTERVAL,
    RECONNECT_DELAYS,
)
from .models import MLControlCommand, MLControlResult, MLDevice, MLDeviceState

_LOGGER = logging.getLogger(__name__)


class MLClientProtocol(Protocol):
    """Client surface consumed by the coordinator and its fake tests."""

    async def connect(self) -> None: ...

    async def register(self) -> object: ...

    async def sync_devices(self) -> tuple[MLDevice, ...]: ...

    async def sync_states(self) -> tuple[MLDeviceState, ...]: ...

    async def control(self, command: MLControlCommand) -> MLControlResult: ...

    async def next_event(self, timeout: float | None = None) -> MLDeviceState: ...

    async def close(self) -> None: ...


class MLCoordinatorState(Enum):
    """Explicit lifecycle states for one gateway runtime."""

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    REGISTERING = "registering"
    SYNCING_DEVICES = "syncing_devices"
    SYNCING_STATES = "syncing_states"
    READY = "ready"


@dataclass(frozen=True, slots=True)
class CoordinatorData:
    """Immutable coordinator snapshot exposed to mapping and entities."""

    devices: Mapping[str, MLDevice]
    states: Mapping[str, Mapping[str, str]]
    connected: bool
    registered: bool
    synchronized: bool
    last_update: datetime | None


@dataclass(frozen=True, slots=True)
class MLDirectoryDiff:
    """One device directory transition, retaining both old and new models."""

    added: Mapping[str, MLDevice]
    changed: Mapping[str, tuple[MLDevice, MLDevice]]
    removed: Mapping[str, MLDevice]

    @property
    def has_changes(self) -> bool:
        return bool(self.added or self.changed or self.removed)


class MLCoordinatorUnavailableError(MLConnectionError):
    """A control was attempted while the gateway snapshot was unavailable."""


class MLControlConfirmationTimeout(MLTimeoutError):
    """A command was accepted but its requested state could not be confirmed."""


StateListener = Callable[[], None]
DirectoryListener = Callable[[MLDirectoryDiff], None]
MLEventListener = Callable[[MLDeviceState], None]
ClientFactory = Callable[[], MLClientProtocol]


@dataclass(slots=True)
class _ControlWaiter:
    command: MLControlCommand
    future: asyncio.Future[None]


class MLCoordinator:
    """Own the single trustworthy runtime cache for one ML Config Entry."""

    def __init__(
        self,
        client_factory: ClientFactory,
        *,
        reconnect_delays: Sequence[float] = RECONNECT_DELAYS,
        device_refresh_interval: float = DEVICE_REFRESH_INTERVAL,
        control_confirmation_timeout: float = CONTROL_CONFIRMATION_TIMEOUT,
    ) -> None:
        delays = tuple(float(delay) for delay in reconnect_delays)
        if not delays or any(delay < 0 for delay in delays):
            raise ValueError("reconnect_delays must contain non-negative values")
        if device_refresh_interval <= 0 or control_confirmation_timeout <= 0:
            raise ValueError("coordinator intervals must be positive")

        self._client_factory = client_factory
        self._reconnect_delays = delays
        self._device_refresh_interval = device_refresh_interval
        self._control_confirmation_timeout = control_confirmation_timeout

        self._state = MLCoordinatorState.DISCONNECTED
        self._client: MLClientProtocol | None = None
        self._devices: dict[str, MLDevice] = {}
        self._states: dict[str, dict[str, str]] = {}
        # Only push-event revisions are tracked. Full snapshots must not mask a
        # later full snapshot when a control recheck and directory refresh race.
        self._event_revisions: dict[tuple[str, str], int] = {}
        self._revision = 0
        self._last_update: datetime | None = None

        self._state_listeners: set[StateListener] = set()
        self._directory_listeners: set[DirectoryListener] = set()
        self._event_listeners: set[MLEventListener] = set()
        self._control_waiters: dict[tuple[str, str], list[_ControlWaiter]] = {}
        self._supervisor_task: asyncio.Task[None] | None = None
        self._session_tasks: set[asyncio.Task[None]] = set()
        self._lifecycle_lock = asyncio.Lock()
        self._stopping = False

    @property
    def state(self) -> MLCoordinatorState:
        return self._state

    @property
    def client(self) -> MLClientProtocol | None:
        """Return the current connection client for diagnostics only."""
        return self._client

    @property
    def data(self) -> CoordinatorData:
        """Return a copy-on-read immutable view of raw ML data."""
        devices = MappingProxyType(dict(self._devices))
        states = MappingProxyType(
            {
                device_id: MappingProxyType(dict(values))
                for device_id, values in self._states.items()
            }
        )
        return CoordinatorData(
            devices=devices,
            states=states,
            connected=self._state
            not in {MLCoordinatorState.DISCONNECTED, MLCoordinatorState.CONNECTING},
            registered=self._state
            in {
                MLCoordinatorState.SYNCING_DEVICES,
                MLCoordinatorState.SYNCING_STATES,
                MLCoordinatorState.READY,
            },
            synchronized=self._state is MLCoordinatorState.READY,
            last_update=self._last_update,
        )

    @property
    def background_tasks(self) -> tuple[asyncio.Task[None], ...]:
        """Expose owned tasks for diagnostics and leak assertions."""
        tasks = set(self._session_tasks)
        if self._supervisor_task is not None:
            tasks.add(self._supervisor_task)
        return tuple(tasks)

    def async_add_listener(self, listener: StateListener) -> Callable[[], None]:
        """Subscribe to cache and availability updates."""
        self._state_listeners.add(listener)
        return lambda: self._state_listeners.discard(listener)

    def async_add_directory_listener(
        self, listener: DirectoryListener
    ) -> Callable[[], None]:
        """Subscribe to added, changed, and removed device snapshots."""
        self._directory_listeners.add(listener)
        return lambda: self._directory_listeners.discard(listener)

    def async_add_event_listener(
        self, listener: MLEventListener
    ) -> Callable[[], None]:
        """Subscribe to one real-time appwsserver status notification.

        Full state synchronization deliberately does not invoke these listeners:
        consumers such as panel-action entities must not replay stale snapshots
        as new physical actions.
        """
        self._event_listeners.add(listener)
        return lambda: self._event_listeners.discard(listener)

    async def async_start(self) -> None:
        """Complete the initial full sync and start runtime supervision."""
        async with self._lifecycle_lock:
            if self._supervisor_task is not None and not self._supervisor_task.done():
                return
            self._stopping = False
            try:
                client = await self._establish()
            except BaseException:
                await self._close_current_client()
                self._set_state(MLCoordinatorState.DISCONNECTED)
                raise
            self._supervisor_task = asyncio.create_task(
                self._supervise(client), name="ml_localservice_coordinator_supervisor"
            )

    async def async_shutdown(self) -> None:
        """Stop reconnection, refresh, event, and control-confirmation work."""
        async with self._lifecycle_lock:
            self._stopping = True
            error = MLCoordinatorUnavailableError("ML coordinator was unloaded")
            self._fail_control_waiters(error)
            task = self._supervisor_task
            self._supervisor_task = None
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            await self._close_current_client()
            self._set_state(MLCoordinatorState.DISCONNECTED)

    async def async_control(
        self,
        command: MLControlCommand,
        *,
        wait_for_confirmation: bool = False,
        confirmation_timeout: float | None = None,
    ) -> MLControlResult:
        """Return gateway acceptance, optionally requiring state confirmation."""
        if not isinstance(command, MLControlCommand):
            raise TypeError("command must be MLControlCommand")
        timeout = (
            self._control_confirmation_timeout
            if confirmation_timeout is None
            else confirmation_timeout
        )
        if timeout <= 0:
            raise ValueError("confirmation_timeout must be positive")
        if self._state is not MLCoordinatorState.READY or self._client is None:
            raise MLCoordinatorUnavailableError("ML gateway is not synchronized")

        device = self._devices.get(command.c_dev_id)
        if device is None:
            raise ValueError(f"unknown ML device {command.c_dev_id}")
        function = next(
            (item for item in device.functions if item.fun_no == command.fun_no), None
        )
        if function is None:
            raise ValueError(
                f"unknown ML function {command.fun_no} for {command.c_dev_id}"
            )
        if not function.writable:
            raise ValueError(
                f"ML function {command.fun_no} for {command.c_dev_id} is read-only"
            )

        client = self._client
        # Acceptance is enough for normal HA actions. Real telemetry continues
        # through the event stream; never predict state from a control request.
        if not wait_for_confirmation:
            return await client.control(command)
        loop = asyncio.get_running_loop()
        future: asyncio.Future[None] = loop.create_future()
        key = (command.c_dev_id, command.fun_no)
        waiter = _ControlWaiter(command, future)
        self._control_waiters.setdefault(key, []).append(waiter)
        try:
            result = await client.control(command)
            try:
                async with asyncio.timeout(timeout):
                    await future
                return result
            except TimeoutError:
                pass

            # ctrlDevResult only acknowledges acceptance. One full snapshot is
            # the bounded fallback when the expected push was not observed.
            revision = self._revision
            states = await client.sync_states()
            self._replace_states(states, preserve_after=revision)
            self._notify_state_listeners()
            current = self._states.get(command.c_dev_id, {}).get(command.fun_no)
            if command.matches_state(current):
                return result
            raise MLControlConfirmationTimeout(
                f"control accepted but {command.c_dev_id}/{command.fun_no} "
                f"did not reach {command.fun_val!r}"
            )
        finally:
            self._remove_control_waiter(key, waiter)

    async def _establish(self) -> MLClientProtocol:
        client = self._client_factory()
        self._client = client
        try:
            self._set_state(MLCoordinatorState.CONNECTING)
            await client.connect()
            self._set_state(MLCoordinatorState.REGISTERING)
            await client.register()
            self._set_state(MLCoordinatorState.SYNCING_DEVICES)
            devices = await client.sync_devices()
            self._set_state(MLCoordinatorState.SYNCING_STATES)
            states = await client.sync_states()
            diff = self._replace_catalog(devices, states)
            self._set_state(MLCoordinatorState.READY)
            self._notify_directory_listeners(diff)
            return client
        except BaseException:
            try:
                await client.close()
            except Exception:
                _LOGGER.debug(
                    "Failed to close an incomplete ML connection", exc_info=True
                )
            if self._client is client:
                self._client = None
            raise

    async def _supervise(self, client: MLClientProtocol) -> None:
        try:
            while not self._stopping:
                try:
                    await self._run_connected(client)
                except asyncio.CancelledError:
                    raise
                except MLClientError as err:
                    _LOGGER.warning(
                        "ML runtime connection ended (%s); starting full recovery", err
                    )
                except Exception:
                    _LOGGER.warning(
                        "ML runtime connection ended; starting full recovery",
                        exc_info=True,
                    )
                await self._disconnect_client(client)
                if self._stopping:
                    return

                attempt = 0
                while not self._stopping:
                    delay = self._reconnect_delays[
                        min(attempt, len(self._reconnect_delays) - 1)
                    ]
                    await asyncio.sleep(delay)
                    if self._stopping:
                        return
                    try:
                        client = await self._establish()
                    except asyncio.CancelledError:
                        raise
                    except MLClientError as err:
                        self._set_state(MLCoordinatorState.DISCONNECTED)
                        _LOGGER.warning(
                            "ML gateway recovery attempt failed (%s); retrying in "
                            "bounded backoff",
                            err,
                        )
                        attempt += 1
                        continue
                    except Exception:
                        self._set_state(MLCoordinatorState.DISCONNECTED)
                        _LOGGER.warning(
                            "ML gateway recovery attempt failed; retrying in "
                            "bounded backoff",
                            exc_info=True,
                        )
                        attempt += 1
                        continue
                    break
        finally:
            await self._cancel_session_tasks()

    async def _run_connected(self, client: MLClientProtocol) -> None:
        event_task = asyncio.create_task(
            self._consume_events(client), name="ml_localservice_status_events"
        )
        refresh_task = asyncio.create_task(
            self._refresh_directory(client), name="ml_localservice_device_refresh"
        )
        self._session_tasks.update((event_task, refresh_task))
        try:
            done, _ = await asyncio.wait(
                (event_task, refresh_task), return_when=asyncio.FIRST_COMPLETED
            )
            completed = done.pop()
            exception = completed.exception()
            if exception is not None:
                raise exception
            raise MLConnectionError("ML runtime worker ended unexpectedly")
        finally:
            await self._cancel_session_tasks()

    async def _consume_events(self, client: MLClientProtocol) -> None:
        while True:
            event = await client.next_event()
            if event.c_dev_id not in self._devices:
                _LOGGER.debug(
                    "Ignoring status for unknown ML device %s", event.c_dev_id
                )
                continue
            self._revision += 1
            self._states.setdefault(event.c_dev_id, {})[event.fun_no] = event.fun_val
            self._event_revisions[(event.c_dev_id, event.fun_no)] = self._revision
            self._last_update = datetime.now(UTC)
            self._confirm_control_waiters(event)
            self._notify_event_listeners(event)
            self._notify_state_listeners()

    async def _refresh_directory(self, client: MLClientProtocol) -> None:
        while True:
            await asyncio.sleep(self._device_refresh_interval)
            devices = await client.sync_devices()
            diff = self._directory_diff(devices)
            if not diff.has_changes:
                continue
            revision = self._revision
            states = await client.sync_states()
            diff = self._replace_catalog(
                devices, states, preserve_after=revision
            )
            self._notify_directory_listeners(diff)
            self._notify_state_listeners()

    def _replace_catalog(
        self,
        devices: Sequence[MLDevice],
        states: Sequence[MLDeviceState],
        *,
        preserve_after: int | None = None,
    ) -> MLDirectoryDiff:
        diff = self._replace_devices(devices)
        self._replace_states(states, preserve_after=preserve_after)
        return diff

    def _directory_diff(self, devices: Sequence[MLDevice]) -> MLDirectoryDiff:
        new = {device.c_dev_id: device for device in devices}
        old = self._devices
        added = {device_id: new[device_id] for device_id in new.keys() - old.keys()}
        removed = {device_id: old[device_id] for device_id in old.keys() - new.keys()}
        changed = {
            device_id: (old[device_id], new[device_id])
            for device_id in old.keys() & new.keys()
            if old[device_id] != new[device_id]
        }
        return MLDirectoryDiff(
            MappingProxyType(added),
            MappingProxyType(changed),
            MappingProxyType(removed),
        )

    def _replace_devices(self, devices: Sequence[MLDevice]) -> MLDirectoryDiff:
        diff = self._directory_diff(devices)
        new = {device.c_dev_id: device for device in devices}
        self._devices = new
        for device_id in diff.removed:
            self._states.pop(device_id, None)
            for key in tuple(self._event_revisions):
                if key[0] == device_id:
                    self._event_revisions.pop(key, None)
        self._last_update = datetime.now(UTC)
        return diff

    def _replace_states(
        self,
        states: Sequence[MLDeviceState],
        *,
        preserve_after: int | None,
    ) -> None:
        replacement: dict[str, dict[str, str]] = {
            device_id: {} for device_id in self._devices
        }
        for state in states:
            if state.c_dev_id not in self._devices:
                continue
            replacement[state.c_dev_id][state.fun_no] = state.fun_val

        preserved: dict[tuple[str, str], int] = {}
        if preserve_after is not None:
            for key, revision in self._event_revisions.items():
                if revision <= preserve_after or key[0] not in replacement:
                    continue
                current = self._states.get(key[0], {}).get(key[1])
                if current is not None:
                    replacement[key[0]][key[1]] = current
                    preserved[key] = revision

        self._revision += 1
        self._states = replacement
        self._event_revisions = preserved
        self._last_update = datetime.now(UTC)

    async def _disconnect_client(self, client: MLClientProtocol) -> None:
        self._set_state(MLCoordinatorState.DISCONNECTED)
        self._fail_control_waiters(
            MLCoordinatorUnavailableError("ML gateway connection was lost")
        )
        try:
            await client.close()
        except Exception:
            _LOGGER.debug("Failed to close disconnected ML client", exc_info=True)
        if self._client is client:
            self._client = None

    async def _close_current_client(self) -> None:
        client = self._client
        self._client = None
        if client is not None:
            try:
                await client.close()
            except Exception:
                _LOGGER.debug("Failed to close ML client", exc_info=True)

    async def _cancel_session_tasks(self) -> None:
        current = asyncio.current_task()
        tasks = tuple(self._session_tasks)
        self._session_tasks.clear()
        for task in tasks:
            if task is not current and not task.done():
                task.cancel()
        await asyncio.gather(
            *(task for task in tasks if task is not current), return_exceptions=True
        )

    def _set_state(self, state: MLCoordinatorState) -> None:
        changed = self._state is not state
        self._state = state
        if changed:
            self._notify_state_listeners()

    def _notify_state_listeners(self) -> None:
        for listener in tuple(self._state_listeners):
            try:
                listener()
            except Exception:
                _LOGGER.exception("ML coordinator state listener failed")

    def _notify_directory_listeners(self, diff: MLDirectoryDiff) -> None:
        if not diff.has_changes:
            return
        for listener in tuple(self._directory_listeners):
            try:
                listener(diff)
            except Exception:
                _LOGGER.exception("ML coordinator directory listener failed")

    def _notify_event_listeners(self, event: MLDeviceState) -> None:
        """Deliver a raw push notification without replaying snapshot values."""
        for listener in tuple(self._event_listeners):
            try:
                listener(event)
            except Exception:
                _LOGGER.exception("ML coordinator event listener failed")

    def _confirm_control_waiters(self, event: MLDeviceState) -> None:
        key = (event.c_dev_id, event.fun_no)
        for waiter in tuple(self._control_waiters.get(key, ())):
            if waiter.command.matches_state(event.fun_val) and not waiter.future.done():
                waiter.future.set_result(None)

    def _remove_control_waiter(
        self, key: tuple[str, str], waiter: _ControlWaiter
    ) -> None:
        waiters = self._control_waiters.get(key)
        if waiters is None:
            return
        try:
            waiters.remove(waiter)
        except ValueError:
            pass
        if waiter.future.done() and not waiter.future.cancelled():
            waiter.future.exception()
        elif not waiter.future.done():
            waiter.future.cancel()
        if not waiters:
            self._control_waiters.pop(key, None)

    def _fail_control_waiters(self, error: MLClientError) -> None:
        for waiters in tuple(self._control_waiters.values()):
            for waiter in tuple(waiters):
                if not waiter.future.done():
                    waiter.future.set_exception(error)
        self._control_waiters.clear()
