"""Config Entry runtime wiring tests; skipped outside Home Assistant."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import custom_components.ml_localservice as integration
from custom_components.ml_localservice.client import MLConnectionError
from custom_components.ml_localservice.mapping import HACommand
from custom_components.ml_localservice.models import MLDevice, MLDeviceState, MLFunction

try:
    from homeassistant.exceptions import ConfigEntryNotReady
except ModuleNotFoundError:  # pragma: no cover - local stdlib-only check
    ConfigEntryNotReady = None  # type: ignore[assignment,misc]


class RuntimeFakeClient:
    def __init__(self, *, connect_error: Exception | None = None) -> None:
        self.connect_error = connect_error
        self.calls: list[str] = []

    async def connect(self) -> None:
        self.calls.append("connect")
        if self.connect_error is not None:
            raise self.connect_error

    async def register(self) -> object:
        self.calls.append("register")
        return object()

    async def sync_devices(self) -> tuple[MLDevice, ...]:
        self.calls.append("sync_devices")
        return ()

    async def sync_states(self) -> tuple[MLDeviceState, ...]:
        self.calls.append("sync_states")
        return ()

    async def control(self, _command: object) -> object:
        raise AssertionError("control is not expected")

    async def next_event(self, timeout: float | None = None) -> MLDeviceState:
        del timeout
        raise MLConnectionError("fixture stream ended")

    async def close(self) -> None:
        self.calls.append("close")


@unittest.skipIf(ConfigEntryNotReady is None, "Home Assistant is not installed")
class ConfigEntryRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def entry(self) -> SimpleNamespace:
        return SimpleNamespace(
            entry_id="entry-1",
            options={},
            data={
                "host": "192.0.2.10",
                "gateway_id": "fixture-gateway",
                "port": 8820,
                "home_fingerprint": "a" * 32,
            },
            async_on_unload=Mock(),
            add_update_listener=Mock(return_value=lambda: None),
        )

    def hass(self) -> SimpleNamespace:
        return SimpleNamespace(
            config_entries=SimpleNamespace(
                async_forward_entry_setups=AsyncMock(),
                async_unload_platforms=AsyncMock(return_value=True),
                async_reload=AsyncMock(),
            )
        )

    async def test_setup_assigns_runtime_and_unload_stops_coordinator(self) -> None:
        client = RuntimeFakeClient()
        entry = self.entry()
        entry.options = {
            "inverted_position_device_ids": ["curtain"],
            "missing_state_fallback": True,
        }
        hass = self.hass()
        with (
            patch(
                "homeassistant.helpers.aiohttp_client.async_get_clientsession",
                return_value=object(),
            ),
            patch.object(integration, "MLAppWSClient", return_value=client),
        ):
            self.assertTrue(await integration.async_setup_entry(hass, entry))
            self.assertTrue(entry.runtime_data.coordinator.data.synchronized)
            self.assertIs(entry.runtime_data.client, client)
            self.assertEqual(entry.runtime_data.gateway_id, "fixture-gateway")
            self.assertTrue(entry.runtime_data.missing_state_fallback)
            curtain = MLDevice("curtain", "Curtain", "12", "11200001", "", (
                MLFunction("position", "", True, True, "0", "100", None, ""),
            ))
            mapper = entry.runtime_data.registry.resolve(curtain)
            entity, = mapper.map_entities(curtain)
            command, = mapper.map_command(curtain, entity, HACommand("set_cover_position", 25))
            self.assertEqual(command.fun_val, "75")
            hass.config_entries.async_forward_entry_setups.assert_awaited_once()
            self.assertTrue(await integration.async_unload_entry(hass, entry))
            hass.config_entries.async_unload_platforms.assert_awaited_once()

        self.assertEqual(
            client.calls[:4], ["connect", "register", "sync_devices", "sync_states"]
        )
        self.assertIn("close", client.calls)
        self.assertFalse(entry.runtime_data.coordinator.background_tasks)

    async def test_initial_failure_is_config_entry_not_ready_without_task_leak(
        self,
    ) -> None:
        client = RuntimeFakeClient(connect_error=MLConnectionError("offline"))
        entry = self.entry()
        hass = self.hass()
        with (
            patch(
                "homeassistant.helpers.aiohttp_client.async_get_clientsession",
                return_value=object(),
            ),
            patch.object(integration, "MLAppWSClient", return_value=client),
        ):
            with self.assertRaises(ConfigEntryNotReady):
                await integration.async_setup_entry(hass, entry)

        self.assertIn("close", client.calls)
        self.assertFalse(entry.runtime_data.coordinator.background_tasks)
        self.assertTrue(entry.runtime_data.missing_state_fallback)
        curtain = MLDevice("new-curtain", "Curtain", "12", "11200001", "", (
            MLFunction("position", "", True, True, "0", "100", None, ""),
        ))
        mapper = entry.runtime_data.registry.resolve(curtain)
        entity, = mapper.map_entities(curtain)
        command, = mapper.map_command(curtain, entity, HACommand("set_cover_position", 25))
        self.assertEqual(command.fun_val, "75")
        hass.config_entries.async_forward_entry_setups.assert_not_awaited()

    async def test_entry_update_reloads_the_same_config_entry(self) -> None:
        hass = self.hass()
        entry = self.entry()
        await integration._async_update_listener(hass, entry)
        hass.config_entries.async_reload.assert_awaited_once_with("entry-1")

    async def test_platform_setup_failure_reaps_the_started_runtime(self) -> None:
        client = RuntimeFakeClient()
        entry = self.entry()
        hass = self.hass()
        hass.config_entries.async_forward_entry_setups.side_effect = RuntimeError(
            "platform setup failed"
        )
        entry.options = {"missing_state_fallback": False, "inverted_position_device_ids": []}
        with (
            patch(
                "homeassistant.helpers.aiohttp_client.async_get_clientsession",
                return_value=object(),
            ),
            patch.object(integration, "MLAppWSClient", return_value=client),
        ):
            with self.assertRaisesRegex(RuntimeError, "platform setup failed"):
                await integration.async_setup_entry(hass, entry)

        self.assertIn("close", client.calls)
        self.assertFalse(entry.runtime_data.coordinator.background_tasks)

        self.assertFalse(entry.runtime_data.missing_state_fallback)
        curtain = MLDevice("new-curtain", "Curtain", "12", "11200001", "", (
            MLFunction("position", "", True, True, "0", "100", None, ""),
        ))
        mapper = entry.runtime_data.registry.resolve(curtain)
        entity, = mapper.map_entities(curtain)
        command, = mapper.map_command(curtain, entity, HACommand("set_cover_position", 25))
        self.assertEqual(command.fun_val, "25")


if __name__ == "__main__":
    unittest.main()
