"""Pure tests for dynamic HA platform directory reconciliation."""

from __future__ import annotations

import asyncio
import json
import unittest
from pathlib import Path
from types import SimpleNamespace

from custom_components.ml_localservice.mapping.models import HAEntitySpec
from custom_components.ml_localservice.models import MLDevice, parse_devices
from custom_components.ml_localservice.platform import MLPlatformManager
from custom_components.ml_localservice.runtime import MLConfigEntryRuntime

FIXTURE = Path(__file__).with_name("fixtures") / "mapping_devices.json"


def fixture_devices() -> dict[str, MLDevice]:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return {device.c_dev_id: device for device in parse_devices(payload)}


class FakeCoordinator:
    def __init__(self, devices: tuple[MLDevice, ...]) -> None:
        self.set_devices(devices)
        self.listeners = set()

    def set_devices(self, devices: tuple[MLDevice, ...]) -> None:
        self.data = SimpleNamespace(
            devices={device.c_dev_id: device for device in devices}
        )

    def async_add_directory_listener(self, listener):
        self.listeners.add(listener)
        return lambda: self.listeners.discard(listener)

    def emit_directory_change(self) -> None:
        for listener in tuple(self.listeners):
            listener(object())


class FakeHass:
    def __init__(self) -> None:
        self.tasks: list[asyncio.Task[None]] = []

    def async_create_task(self, coroutine, name: str):
        task = asyncio.create_task(coroutine, name=name)
        self.tasks.append(task)
        return task


class FakeEntity:
    def __init__(
        self, runtime: MLConfigEntryRuntime, spec: HAEntitySpec
    ) -> None:
        self.runtime = runtime
        self.spec = spec
        self.removed = False

    @property
    def unique_id(self) -> str:
        return self.spec.unique_id(self.runtime.gateway_id)

    def update_spec(self, spec: HAEntitySpec) -> None:
        self.spec = spec

    async def async_remove(self, *, force_remove: bool = False) -> None:
        self.removed = True
        self.force_removed = force_remove


class PlatformManagerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.devices = fixture_devices()

    def manager(self, devices: tuple[MLDevice, ...], gateway_id: str = "gw-1"):
        coordinator = FakeCoordinator(devices)
        runtime = MLConfigEntryRuntime(coordinator, gateway_id)  # type: ignore[arg-type]
        hass = FakeHass()
        added: list[FakeEntity] = []

        def async_add_entities(entities, update_before_add=False):
            self.assertFalse(update_before_add)
            added.extend(entities)

        manager = MLPlatformManager(
            hass, runtime, "light", async_add_entities, FakeEntity
        )
        return manager, coordinator, hass, added

    async def test_initial_projection_skips_container_and_placeholder(self) -> None:
        manager, coordinator, _hass, added = self.manager(
            (
                self.devices["relay-container"],
                self.devices["unknown-do"],
                self.devices["observed-basic-light"],
            )
        )
        await manager.async_setup()

        self.assertEqual(len(added), 1)
        self.assertEqual(added[0].spec.platform, "light")
        self.assertEqual(
            added[0].unique_id,
            "gw-1:observed-basic-light:light:power",
        )
        self.assertEqual(len(coordinator.listeners), 1)
        await manager.async_shutdown()
        self.assertFalse(coordinator.listeners)

    async def test_type_change_adds_and_removal_can_restore_stable_id(self) -> None:
        placeholder = self.devices["unknown-do"]
        source = self.devices["observed-basic-light"]
        supported = MLDevice(
            placeholder.c_dev_id,
            placeholder.name,
            source.main_type,
            source.sub_type,
            placeholder.room_id,
            source.functions,
        )
        manager, coordinator, hass, added = self.manager((placeholder,))
        await manager.async_setup()
        self.assertEqual(added, [])

        coordinator.set_devices((supported,))
        coordinator.emit_directory_change()
        await asyncio.gather(*hass.tasks)
        self.assertEqual(len(added), 1)
        first = added[0]

        coordinator.set_devices((placeholder,))
        coordinator.emit_directory_change()
        await asyncio.gather(*hass.tasks)
        self.assertTrue(first.removed)
        self.assertFalse(first.force_removed)

        coordinator.set_devices((supported,))
        coordinator.emit_directory_change()
        await asyncio.gather(*hass.tasks)
        self.assertEqual(len(added), 2)
        self.assertEqual(added[0].unique_id, added[1].unique_id)
        await manager.async_shutdown()

    async def test_gateway_identity_isolates_equal_device_ids(self) -> None:
        device = self.devices["observed-basic-light"]
        manager_a, _coordinator_a, _hass_a, added_a = self.manager(
            (device,), "gw-a"
        )
        manager_b, _coordinator_b, _hass_b, added_b = self.manager(
            (device,), "gw-b"
        )
        await manager_a.async_setup()
        await manager_b.async_setup()
        self.assertNotEqual(added_a[0].unique_id, added_b[0].unique_id)
        await manager_a.async_shutdown()
        await manager_b.async_shutdown()


if __name__ == "__main__":
    unittest.main()
