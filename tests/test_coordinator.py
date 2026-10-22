"""Fake-client tests for coordinator cache and lifecycle behavior."""

from __future__ import annotations

import asyncio
import unittest
from collections.abc import Callable, Sequence
from dataclasses import replace

from custom_components.ml_localservice.client import MLConnectionClosedError
from custom_components.ml_localservice.mapping import HACommand, create_default_registry
from custom_components.ml_localservice.coordinator import (
    MLControlConfirmationTimeout,
    MLCoordinator,
    MLCoordinatorState,
    MLDirectoryDiff,
)
from custom_components.ml_localservice.models import (
    MLControlCommand,
    MLControlResult,
    MLDevice,
    MLDeviceState,
    MLFunction,
)


def device(
    *,
    c_dev_id: str = "device-1",
    sub_type: str = "4991543809610",
    writable: bool = True,
) -> MLDevice:
    return MLDevice(
        c_dev_id=c_dev_id,
        name="Fixture relay",
        main_type="10",
        sub_type=sub_type,
        room_id="room-1",
        functions=(
            MLFunction(
                fun_no="switch",
                fun_val="",
                readable=True,
                writable=writable,
                minimum="",
                maximum="",
                range_values=("true", "false"),
                timestamp="",
            ),
        ),
    )


def state(value: str, *, c_dev_id: str = "device-1") -> MLDeviceState:
    return MLDeviceState(c_dev_id, "switch", value, "1785920352000")


def color_temp_device() -> MLDevice:
    source = device(sub_type="10100003")
    return replace(source, functions=source.functions + (
        MLFunction("temp", "", True, True, "2000", "6000", None, ""),
    ))


def temp_state(value: str) -> MLDeviceState:
    return MLDeviceState("device-1", "temp", value, "1785920352000")


def color_temp_command(requested: int = 4000) -> MLControlCommand:
    lamp = color_temp_device()
    mapper = create_default_registry().resolve(lamp)
    command, = mapper.map_command(lamp, mapper.map_entities(lamp)[0], HACommand("set_color_temp", requested))
    return command


class FakeClient:
    def __init__(
        self,
        devices: Sequence[Sequence[MLDevice]],
        states: Sequence[Sequence[MLDeviceState]],
    ) -> None:
        self._devices = [tuple(value) for value in devices]
        self._states = [tuple(value) for value in states]
        self._device_index = 0
        self._state_index = 0
        self.events: asyncio.Queue[MLDeviceState | Exception] = asyncio.Queue()
        self.calls: list[str] = []
        self.closed = False
        self.control_called = asyncio.Event()
        self.on_control: Callable[[MLControlCommand], None] | None = None

    async def connect(self) -> None:
        self.calls.append("connect")

    async def register(self) -> object:
        self.calls.append("register")
        return object()

    async def sync_devices(self) -> tuple[MLDevice, ...]:
        self.calls.append("sync_devices")
        result = self._devices[min(self._device_index, len(self._devices) - 1)]
        self._device_index += 1
        return result

    async def sync_states(self) -> tuple[MLDeviceState, ...]:
        self.calls.append("sync_states")
        result = self._states[min(self._state_index, len(self._states) - 1)]
        self._state_index += 1
        return result

    async def control(self, command: MLControlCommand) -> MLControlResult:
        self.calls.append("control")
        self.control_called.set()
        if self.on_control is not None:
            self.on_control(command)
        return MLControlResult(True, "ok", "control-1", "1785920353000")

    async def next_event(self, timeout: float | None = None) -> MLDeviceState:
        del timeout
        item = await self.events.get()
        if isinstance(item, Exception):
            raise item
        return item

    async def close(self) -> None:
        self.calls.append("close")
        self.closed = True


async def wait_until(predicate: Callable[[], bool], timeout: float = 0.5) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0)


class CoordinatorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.coordinators: list[MLCoordinator] = []

    async def asyncTearDown(self) -> None:
        for coordinator in self.coordinators:
            await coordinator.async_shutdown()

    def coordinator(
        self,
        clients: Sequence[FakeClient],
        *,
        refresh_interval: float = 3600,
        confirmation_timeout: float = 0.01,
        reconnect_delays: Sequence[float] = (0,),
    ) -> MLCoordinator:
        available = iter(clients)
        coordinator = MLCoordinator(
            lambda: next(available),
            reconnect_delays=reconnect_delays,
            device_refresh_interval=refresh_interval,
            control_confirmation_timeout=confirmation_timeout,
        )
        self.coordinators.append(coordinator)
        return coordinator

    async def test_initial_sync_and_notifications_publish_immutable_snapshots(
        self,
    ) -> None:
        client = FakeClient([(device(),)], [(state("false"),)])
        coordinator = self.coordinator([client])
        updates = 0

        def updated() -> None:
            nonlocal updates
            updates += 1

        coordinator.async_add_listener(updated)
        await coordinator.async_start()

        self.assertEqual(
            client.calls[:4], ["connect", "register", "sync_devices", "sync_states"]
        )
        self.assertEqual(coordinator.state, MLCoordinatorState.READY)
        self.assertTrue(coordinator.data.synchronized)
        self.assertEqual(coordinator.data.states["device-1"]["switch"], "false")
        device_states = coordinator.data.states["device-1"]
        with self.assertRaises(TypeError):
            device_states["switch"] = "mutated"  # type: ignore[index]

        # An unknown device must not terminate the event consumer.
        await client.events.put(state("true", c_dev_id="unknown"))
        await client.events.put(state("true"))
        await wait_until(
            lambda: coordinator.data.states["device-1"]["switch"] == "true"
        )
        self.assertGreater(updates, 0)

    async def test_raw_event_listeners_receive_each_push_but_not_sync_snapshots(
        self,
    ) -> None:
        client = FakeClient([(device(),)], [(state("false"),)])
        coordinator = self.coordinator([client])
        received: list[MLDeviceState] = []
        coordinator.async_add_event_listener(received.append)

        await coordinator.async_start()
        self.assertEqual(received, [])

        first = state("true")
        second = state("true")
        await client.events.put(first)
        await client.events.put(second)
        await wait_until(lambda: len(received) == 2)

        self.assertEqual(received, [first, second])

    async def test_disconnect_marks_unavailable_and_full_reconnect_restores_state(
        self,
    ) -> None:
        first = FakeClient([(device(),)], [(state("false"),)])
        recovered = FakeClient([(device(),)], [(state("true"),)])
        coordinator = self.coordinator([first, recovered])
        availability: list[bool] = []
        coordinator.async_add_listener(
            lambda: availability.append(coordinator.data.synchronized)
        )
        await coordinator.async_start()

        await first.events.put(MLConnectionClosedError("fixture disconnect"))
        await wait_until(
            lambda: coordinator.client is recovered
            and coordinator.state is MLCoordinatorState.READY
        )

        self.assertTrue(first.closed)
        self.assertIn(False, availability)
        self.assertEqual(coordinator.data.states["device-1"]["switch"], "true")
        self.assertEqual(
            recovered.calls[:4],
            ["connect", "register", "sync_devices", "sync_states"],
        )

    async def test_directory_type_change_emits_diff_and_resynchronizes_state(
        self,
    ) -> None:
        placeholder = device()
        relay = device(sub_type="10100001")
        second = device(c_dev_id="device-2", sub_type="10100001")
        client = FakeClient(
            [(placeholder,), (relay, second), (second,)],
            [
                (state("false"),),
                (state("true"), state("false", c_dev_id="device-2")),
                (state("true", c_dev_id="device-2"),),
            ],
        )
        coordinator = self.coordinator([client], refresh_interval=0.01)
        changed = asyncio.Event()
        removed = asyncio.Event()
        diffs: list[MLDirectoryDiff] = []
        changed_snapshot: tuple[str, str] | None = None

        def directory_updated(diff: MLDirectoryDiff) -> None:
            nonlocal changed_snapshot
            diffs.append(diff)
            if diff.changed:
                changed_snapshot = (
                    coordinator.data.devices["device-1"].sub_type,
                    coordinator.data.states["device-1"]["switch"],
                )
                changed.set()
            if diff.removed:
                removed.set()

        coordinator.async_add_directory_listener(directory_updated)
        await coordinator.async_start()
        await asyncio.wait_for(changed.wait(), 0.5)
        await asyncio.wait_for(removed.wait(), 0.5)

        change_diff = next(diff for diff in diffs if diff.changed)
        removal_diff = next(diff for diff in diffs if diff.removed)
        self.assertEqual(
            change_diff.changed["device-1"][0].sub_type, "4991543809610"
        )
        self.assertEqual(change_diff.changed["device-1"][1].sub_type, "10100001")
        self.assertIn("device-2", change_diff.added)
        self.assertEqual(changed_snapshot, ("10100001", "true"))
        self.assertIn("device-1", removal_diff.removed)
        self.assertNotIn("device-1", coordinator.data.devices)

    async def test_control_waits_for_matching_real_notification(self) -> None:
        client = FakeClient([(device(),)], [(state("false"),)])
        coordinator = self.coordinator([client], confirmation_timeout=0.2)
        await coordinator.async_start()

        control = asyncio.create_task(
            coordinator.async_control(MLControlCommand("device-1", "switch", "true"))
        )
        await asyncio.wait_for(client.control_called.wait(), 0.2)
        self.assertEqual(coordinator.data.states["device-1"]["switch"], "false")
        self.assertFalse(control.done())

        await client.events.put(state("true"))
        result = await asyncio.wait_for(control, 0.2)
        self.assertTrue(result.success)
        self.assertEqual(coordinator.data.states["device-1"]["switch"], "true")

    async def test_color_temperature_tolerance_waits_for_new_matching_push(self) -> None:
        for matching in ("3900", "4100"):
            with self.subTest(matching=matching):
                # The already-matching cache cannot confirm the new control.
                client = FakeClient([(color_temp_device(),)], [(temp_state("4000"),)])
                coordinator = self.coordinator([client], confirmation_timeout=0.5)
                await coordinator.async_start()
                control = asyncio.create_task(coordinator.async_control(color_temp_command()))
                await asyncio.wait_for(client.control_called.wait(), 0.2)
                self.assertFalse(control.done())
                for invalid in ("3899", "4101", "bad", "NaN"):
                    await client.events.put(temp_state(invalid))
                    await wait_until(lambda: coordinator.data.states["device-1"].get("temp") == invalid)
                    self.assertFalse(control.done(), invalid)
                await client.events.put(temp_state(matching))
                self.assertTrue((await asyncio.wait_for(control, 0.2)).success)
                self.assertEqual(client.calls.count("sync_states"), 1)
                self.assertEqual(coordinator.data.states["device-1"]["temp"], matching)

    async def test_color_temperature_recheck_uses_same_tolerance(self) -> None:
        for value in ("3900", "4100", "3899", "4101", "bad", None):
            with self.subTest(value=value):
                snapshot = () if value is None else (temp_state(value),)
                client = FakeClient([(color_temp_device(),)], [(temp_state("4000"),), snapshot])
                coordinator = self.coordinator([client])
                await coordinator.async_start()
                if value in ("3900", "4100"):
                    self.assertTrue((await coordinator.async_control(color_temp_command())).success)
                else:
                    with self.assertRaises(MLControlConfirmationTimeout):
                        await coordinator.async_control(color_temp_command())
                self.assertEqual(client.calls.count("sync_states"), 2)
                self.assertFalse(coordinator._control_waiters)

    async def test_clamped_color_temperature_confirms_adjusted_target(self) -> None:
        client = FakeClient([(color_temp_device(),)], [(temp_state("4000"),)])
        client.on_control = lambda command: client.events.put_nowait(temp_state("5900"))
        coordinator = self.coordinator([client], confirmation_timeout=0.5)
        await coordinator.async_start()
        self.assertTrue((await coordinator.async_control(color_temp_command(6535))).success)
        self.assertEqual(client.calls.count("sync_states"), 1)

    async def test_inverted_curtain_control_confirms_ml_value_and_projects_ha_value(self) -> None:
        curtain = MLDevice("curtain", "Curtain", "12", "11200001", "", (
            MLFunction("position", "", True, True, "0", "100", None, ""),
        ))
        registry = create_default_registry(inverted_position_device_ids=frozenset({"curtain"}))
        mapper = registry.resolve(curtain)
        entity, = mapper.map_entities(curtain)
        command, = mapper.map_command(curtain, entity, HACommand("set_cover_position", 25))
        for use_push in (True, False):
            with self.subTest(use_push=use_push):
                initial = MLDeviceState("curtain", "position", "100", "1")
                target = MLDeviceState("curtain", "position", "75", "2")
                client = FakeClient([(curtain,)], [(initial,), (target,)])
                if use_push:
                    client.on_control = lambda sent: client.events.put_nowait(target)
                coordinator = self.coordinator([client])
                await coordinator.async_start()
                self.assertTrue((await coordinator.async_control(command)).success)
                self.assertEqual(command.fun_val, "75")
                self.assertEqual(client.calls.count("sync_states"), 1 if use_push else 2)
                projected, = mapper.map_state(curtain, coordinator.data.states["curtain"])
                self.assertEqual(projected.attributes["current_cover_position"], 25)

    async def test_numeric_control_without_tolerance_still_requires_exact_match(self) -> None:
        client = FakeClient([(color_temp_device(),)], [(temp_state("3900"),)])
        coordinator = self.coordinator([client])
        await coordinator.async_start()
        with self.assertRaises(MLControlConfirmationTimeout):
            await coordinator.async_control(MLControlCommand("device-1", "temp", "4000"))

    async def test_control_timeout_performs_one_full_state_recheck(self) -> None:
        client = FakeClient(
            [(device(),)],
            [(state("false"),), (state("true"),)],
        )
        coordinator = self.coordinator([client])
        await coordinator.async_start()

        result = await coordinator.async_control(
            MLControlCommand("device-1", "switch", "true")
        )
        self.assertTrue(result.success)
        self.assertEqual(client.calls.count("sync_states"), 2)

    async def test_control_timeout_is_explicit_when_recheck_does_not_match(
        self,
    ) -> None:
        client = FakeClient(
            [(device(),)],
            [(state("false"),), (state("false"),)],
        )
        coordinator = self.coordinator([client])
        await coordinator.async_start()

        with self.assertRaises(MLControlConfirmationTimeout):
            await coordinator.async_control(
                MLControlCommand("device-1", "switch", "true")
            )
        self.assertEqual(client.calls.count("sync_states"), 2)

    async def test_shutdown_cancels_pending_reconnect_and_all_runtime_tasks(
        self,
    ) -> None:
        first = FakeClient([(device(),)], [(state("false"),)])
        never_used = FakeClient([(device(),)], [(state("true"),)])
        coordinator = self.coordinator(
            [first, never_used], reconnect_delays=(30,), refresh_interval=30
        )
        await coordinator.async_start()
        owned_tasks = coordinator.background_tasks

        await first.events.put(MLConnectionClosedError("fixture disconnect"))
        await wait_until(lambda: coordinator.state is MLCoordinatorState.DISCONNECTED)
        await coordinator.async_shutdown()

        self.assertTrue(first.closed)
        self.assertEqual(never_used.calls, [])
        self.assertFalse(coordinator.background_tasks)
        self.assertTrue(all(task.done() for task in owned_tasks))


if __name__ == "__main__":
    unittest.main()
