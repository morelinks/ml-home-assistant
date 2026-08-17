"""HA-facing entity and diagnostics tests; skipped without HA dependencies."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from custom_components.ml_localservice.models import (
    MLDevice,
    MLDeviceState,
    MLFunction,
    parse_devices,
)

try:
    from homeassistant.components.light import ColorMode
    from homeassistant.components.climate import ClimateEntityFeature, HVACMode
    from homeassistant.components.fan import FanEntityFeature
    from homeassistant.components.cover import CoverEntityFeature
    from homeassistant.components.binary_sensor import BinarySensorDeviceClass
    from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
    from homeassistant.exceptions import HomeAssistantError

    from custom_components.ml_localservice.diagnostics import (
        async_get_config_entry_diagnostics,
    )
    from custom_components.ml_localservice.binary_sensor import MLBinarySensorEntity
    from custom_components.ml_localservice.light import MLLightEntity
    from custom_components.ml_localservice.lock import MLLockEntity
    from custom_components.ml_localservice.sensor import MLSensorEntity
    from custom_components.ml_localservice.switch import MLSwitchEntity
    from custom_components.ml_localservice.climate import MLClimateEntity
    from custom_components.ml_localservice.fan import MLFanEntity
    from custom_components.ml_localservice.cover import MLCoverEntity
    from custom_components.ml_localservice.event import MLEventEntity
    from custom_components.ml_localservice.select import MLSelectEntity
    from custom_components.ml_localservice.runtime import MLConfigEntryRuntime
except ModuleNotFoundError:  # pragma: no cover - local stdlib-only check
    HomeAssistantError = None  # type: ignore[assignment,misc]
    MLLightEntity = None  # type: ignore[assignment,misc]
    MLBinarySensorEntity = None  # type: ignore[assignment,misc]
    MLLockEntity = None  # type: ignore[assignment,misc]
    MLEventEntity = None  # type: ignore[assignment,misc]
    MLSensorEntity = None  # type: ignore[assignment,misc]
    MLSwitchEntity = None  # type: ignore[assignment,misc]
    MLClimateEntity = None  # type: ignore[assignment,misc]
    MLFanEntity = None  # type: ignore[assignment,misc]
    MLCoverEntity = None  # type: ignore[assignment,misc]
    MLSelectEntity = None  # type: ignore[assignment,misc]

FIXTURE = Path(__file__).with_name("fixtures") / "mapping_devices.json"


class FakeCoordinator:
    def __init__(self, device) -> None:
        self.device = device
        self.commands = []
        self.set_state("false")

    def set_state(self, value: str, *, synchronized: bool = True) -> None:
        self.set_states({"switch": value}, synchronized=synchronized)

    def set_states(self, states: dict[str, str], *, synchronized: bool = True) -> None:
        self.data = SimpleNamespace(
            devices={self.device.c_dev_id: self.device},
            states={self.device.c_dev_id: states},
            synchronized=synchronized,
            connected=synchronized,
            registered=synchronized,
            last_update=None,
        )

    async def async_control(self, command) -> None:
        self.commands.append(command)

    def async_add_listener(self, _listener):
        return lambda: None

    @property
    def state(self):
        return SimpleNamespace(value="ready")

    @property
    def background_tasks(self):
        return ()


@unittest.skipIf(MLLightEntity is None, "Home Assistant is not installed")
class EntityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
        devices = {device.c_dev_id: device for device in parse_devices(payload)}
        self.device = devices["observed-basic-light"]
        self.coordinator = FakeCoordinator(self.device)
        self.runtime = MLConfigEntryRuntime(  # type: ignore[arg-type]
            self.coordinator, "gateway-1"
        )
        spec = self.runtime.registry.describe(self.device).entities[0]
        self.entity = MLLightEntity(self.runtime, spec)

    async def test_state_availability_device_info_and_control(self) -> None:
        self.assertFalse(self.entity.is_on)
        self.assertTrue(self.entity.available)
        self.assertEqual(self.entity.supported_color_modes, {ColorMode.ONOFF})
        self.assertEqual(
            self.entity.device_info["identifiers"],
            {("ml_localservice", "gateway-1:observed-basic-light")},
        )

        self.coordinator.set_state("true")
        self.assertTrue(self.entity.is_on)
        await self.entity.async_turn_on(flash="short")
        await self.entity.async_turn_off()
        self.assertEqual(
            [(command.fun_no, command.fun_val) for command in self.coordinator.commands],
            [("switch", "true"), ("twinkle", "true"), ("switch", "false")],
        )

    async def test_invalid_or_unsynchronized_state_is_unavailable(self) -> None:
        self.coordinator.set_state("invalid")
        self.assertFalse(self.entity.available)
        self.assertIsNone(self.entity.is_on)
        self.assertEqual(
            self.entity.extra_state_attributes,
            {"invalid_ml_value": "invalid"},
        )

        self.coordinator.set_state("true", synchronized=False)
        self.assertFalse(self.entity.available)

    async def test_missing_device_control_raises_ha_error(self) -> None:
        self.coordinator.data.devices.clear()
        with self.assertRaises(HomeAssistantError):
            await self.entity.async_turn_on()

    async def test_extended_light_lock_and_panel_event_entities(self) -> None:
        payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
        devices = {device.c_dev_id: device for device in parse_devices(payload)}

        color_light = devices["color-temp-light"]
        light_coordinator = FakeCoordinator(color_light)
        light_coordinator.set_states({"switch": "true", "bright": "50", "temp": "4000"})
        light_runtime = MLConfigEntryRuntime(light_coordinator, "gateway-1")  # type: ignore[arg-type]
        light_spec = light_runtime.registry.describe(color_light).entities[0]
        light = MLLightEntity(light_runtime, light_spec)
        self.assertEqual(light.supported_color_modes, {ColorMode.COLOR_TEMP})
        self.assertEqual((light.brightness, light.color_temp_kelvin), (128, 4000))
        await light.async_turn_on(brightness=255, color_temp_kelvin=3500)
        self.assertEqual(
            [(command.fun_no, command.fun_val) for command in light_coordinator.commands],
            [("switch", "true"), ("bright", "100"), ("temp", "3500")],
        )

        rgb_light = devices["rgb-light"]
        rgb_coordinator = FakeCoordinator(rgb_light)
        rgb_coordinator.set_states(
            {"switch": "true", "bright": "50", "rgb": "#12AbF0"}
        )
        rgb_runtime = MLConfigEntryRuntime(rgb_coordinator, "gateway-1")  # type: ignore[arg-type]
        rgb_spec = rgb_runtime.registry.describe(rgb_light).entities[0]
        rgb = MLLightEntity(rgb_runtime, rgb_spec)
        self.assertEqual(rgb.supported_color_modes, {ColorMode.RGB})
        self.assertEqual((rgb.brightness, rgb.rgb_color), (128, (18, 171, 240)))
        await rgb.async_turn_on(brightness=128, rgb_color=(255, 0, 16))
        self.assertEqual(
            [(command.fun_no, command.fun_val) for command in rgb_coordinator.commands],
            [("switch", "true"), ("bright", "50"), ("rgb", "#FF0010")],
        )

        lock_device = devices["electric-lock"]
        lock_coordinator = FakeCoordinator(lock_device)
        lock_coordinator.set_states({"status": "true"})
        lock_runtime = MLConfigEntryRuntime(lock_coordinator, "gateway-1")  # type: ignore[arg-type]
        lock = MLLockEntity(lock_runtime, lock_runtime.registry.describe(lock_device).entities[0])
        self.assertTrue(lock.is_locked)
        await lock.async_unlock()
        self.assertEqual(lock_coordinator.commands[0].fun_val, "false")

        panel_device = devices["panel-switch"]
        panel_coordinator = FakeCoordinator(panel_device)
        panel_coordinator.set_states({})
        panel_runtime = MLConfigEntryRuntime(panel_coordinator, "gateway-1")  # type: ignore[arg-type]
        panel_event = MLEventEntity(
            panel_runtime, panel_runtime.registry.describe(panel_device).entities[0]
        )
        self.assertEqual(
            panel_event.event_types, ["click", "double_click", "long_press"]
        )
        self.assertEqual(
            panel_runtime.registry.describe(panel_device).entities[0].commands, ()
        )
        with patch.object(panel_event, "async_write_ha_state") as write_state:
            panel_event._handle_ml_event(
                MLDeviceState(panel_device.c_dev_id, "dbclick", "true", "")
            )
            self.assertEqual(
                panel_event.state_attributes["event_type"], "double_click"
            )
            write_state.assert_called_once()
            panel_event._handle_ml_event(
                MLDeviceState(panel_device.c_dev_id, "click", "false", "")
            )
            write_state.assert_called_once()

    async def test_sensor_and_binary_sensor_platform_entities(self) -> None:
        person = MLDevice(
            "person", "Person detector", "3", "10300002", "", (
                MLFunction("status", "", True, False, "", "", ("true", "false"), ""),
                MLFunction("sensor_mode", "", True, True, "", "", ("0", "1"), ""),
            )
        )
        person_coordinator = FakeCoordinator(person)
        person_coordinator.set_states({"status": "true", "sensor_mode": "1"})
        person_runtime = MLConfigEntryRuntime(person_coordinator, "gateway-1")  # type: ignore[arg-type]
        occupancy, mode = person_runtime.registry.describe(person).entities
        binary_sensor = MLBinarySensorEntity(person_runtime, occupancy)
        switch = MLSwitchEntity(person_runtime, mode)
        self.assertTrue(binary_sensor.is_on)
        self.assertEqual(binary_sensor.device_class, BinarySensorDeviceClass.MOTION)
        self.assertTrue(switch.is_on)
        await switch.async_turn_off()
        self.assertEqual(person_coordinator.commands[0].fun_val, "0")

        temperature = MLDevice(
            "temperature", "Temperature", "3", "10300015", "", (
                MLFunction("temp", "", True, False, "-10", "50", None, ""),
            )
        )
        temperature_coordinator = FakeCoordinator(temperature)
        temperature_coordinator.set_states({"temp": "21.5"})
        temperature_runtime = MLConfigEntryRuntime(temperature_coordinator, "gateway-1")  # type: ignore[arg-type]
        sensor = MLSensorEntity(
            temperature_runtime,
            temperature_runtime.registry.describe(temperature).entities[0],
        )
        self.assertEqual(sensor.native_value, 21.5)
        self.assertEqual(sensor.device_class, SensorDeviceClass.TEMPERATURE)
        self.assertEqual(sensor.native_unit_of_measurement, "°C")
        self.assertEqual(sensor.state_class, SensorStateClass.MEASUREMENT)

    async def test_air_conditioner_climate_and_auxiliary_heat_entities(self) -> None:
        functions = (
            MLFunction("switch", "", True, True, "", "", ("true", "false"), ""),
            MLFunction("mode", "", True, True, "", "", ("0", "1", "2", "3", "16"), ""),
            MLFunction("settemp", "", True, True, "16", "30", None, ""),
            MLFunction("windspeed", "", True, True, "", "", ("0", "1", "2", "3"), ""),
            MLFunction("curtemp", "", True, False, "-10", "40", None, ""),
            MLFunction("hum", "", True, False, "0", "100", None, ""),
            MLFunction("sweep", "", True, True, "", "", ("0", "16"), ""),
            MLFunction("mute_mode", "", True, True, "", "", ("true", "false"), ""),
            MLFunction("aux_heat", "", True, True, "", "", ("0", "1", "2"), ""),
        )
        device = MLDevice("air-conditioner", "Air conditioner", "6", "10600001", "", functions)
        coordinator = FakeCoordinator(device)
        coordinator.set_states(
            {"switch": "true", "mode": "2", "settemp": "24", "windspeed": "3", "curtemp": "22", "hum": "45", "sweep": "16", "mute_mode": "true", "aux_heat": "0"}
        )
        runtime = MLConfigEntryRuntime(coordinator, "gateway-1")  # type: ignore[arg-type]
        climate_spec, mute_spec, aux_heat_spec = runtime.registry.describe(device).entities
        climate = MLClimateEntity(runtime, climate_spec)
        mute = MLSwitchEntity(runtime, mute_spec)
        aux_heat = MLSelectEntity(runtime, aux_heat_spec)
        self.assertEqual(climate.hvac_mode, HVACMode.COOL)
        self.assertEqual(climate.hvac_modes, [HVACMode.OFF, HVACMode.FAN_ONLY, HVACMode.HEAT, HVACMode.COOL, HVACMode.AUTO])
        self.assertEqual((climate.target_temperature, climate.current_temperature, climate.current_humidity), (24.0, 22.0, 45.0))
        self.assertTrue(climate.supported_features & ClimateEntityFeature.TARGET_TEMPERATURE)
        self.assertEqual((climate.fan_mode, climate.swing_mode), ("auto", "paused"))
        await climate.async_set_hvac_mode(HVACMode.HEAT)
        await climate.async_set_temperature(temperature=25)
        await climate.async_set_fan_mode("high")
        await climate.async_set_swing_mode("auto")
        await mute.async_turn_off()
        await aux_heat.async_select_option("on")
        self.assertEqual(aux_heat.options, ["auto", "on", "off"])
        self.assertEqual(
            [(command.fun_no, command.fun_val) for command in coordinator.commands],
            [("switch", "true"), ("mode", "1"), ("settemp", "25"), ("windspeed", "2"), ("sweep", "0"), ("mute_mode", "false"), ("aux_heat", "1")],
        )

    async def test_floor_heat_climate_entity_uses_zero_temperature_bounds(self) -> None:
        device = MLDevice(
            "floor-heat",
            "Floor heating",
            "7",
            "10700001",
            "",
            (
                MLFunction("switch", "", True, True, "", "", ("true", "false"), ""),
                MLFunction("settemp", "", True, True, "0", "100", None, ""),
                MLFunction("curtemp", "", True, False, "0", "100", None, ""),
            ),
        )
        coordinator = FakeCoordinator(device)
        coordinator.set_states({"switch": "true", "settemp": "0", "curtemp": "18.5"})
        runtime = MLConfigEntryRuntime(coordinator, "gateway-1")  # type: ignore[arg-type]
        climate = MLClimateEntity(runtime, runtime.registry.describe(device).entities[0])
        self.assertEqual(climate.hvac_mode, HVACMode.HEAT)
        self.assertEqual(climate.hvac_modes, [HVACMode.OFF, HVACMode.HEAT])
        self.assertEqual((climate.min_temp, climate.max_temp, climate.target_temperature), (0.0, 100.0, 0.0))
        self.assertEqual(climate.current_temperature, 18.5)
        self.assertTrue(climate.supported_features & ClimateEntityFeature.TARGET_TEMPERATURE)
        await climate.async_set_hvac_mode(HVACMode.OFF)
        await climate.async_set_temperature(temperature=55)
        self.assertEqual(
            [(command.fun_no, command.fun_val) for command in coordinator.commands],
            [("switch", "false"), ("settemp", "55")],
        )

    async def test_ventilation_fan_and_environment_sensor_entities(self) -> None:
        device = MLDevice(
            "ventilation",
            "Fresh air",
            "8",
            "10800001",
            "",
            (
                MLFunction("switch", "", True, True, "", "", ("true", "false"), ""),
                MLFunction("windspeed", "", True, True, "", "", ("0", "1", "2"), ""),
                MLFunction("intemp", "", True, False, "-10", "40", None, ""),
                MLFunction("inhum", "", True, False, "0", "100", None, ""),
            ),
        )
        coordinator = FakeCoordinator(device)
        coordinator.set_states(
            {"switch": "true", "windspeed": "2", "intemp": "22.5", "inhum": "45"}
        )
        runtime = MLConfigEntryRuntime(coordinator, "gateway-1")  # type: ignore[arg-type]
        fan_spec, temperature_spec, humidity_spec = runtime.registry.describe(device).entities
        fan = MLFanEntity(runtime, fan_spec)
        temperature = MLSensorEntity(runtime, temperature_spec)
        humidity = MLSensorEntity(runtime, humidity_spec)
        self.assertTrue(fan.is_on)
        self.assertEqual((fan.preset_mode, fan.preset_modes), ("high", ["low", "medium", "high"]))
        self.assertTrue(fan.supported_features & FanEntityFeature.PRESET_MODE)
        self.assertEqual((temperature.native_value, humidity.native_value), (22.5, 45.0))
        await fan.async_turn_on(preset_mode="medium")
        await fan.async_turn_off()
        self.assertEqual(
            [(command.fun_no, command.fun_val) for command in coordinator.commands],
            [("switch", "true"), ("windspeed", "1"), ("switch", "false")],
        )

    async def test_curtain_cover_entity_maps_position_tilt_and_actions(self) -> None:
        device = MLDevice(
            "curtain",
            "Curtain",
            "12",
            "11200007",
            "",
            (
                MLFunction("5", "", True, True, "", "", ("0", "1", "2"), ""),
                MLFunction("2", "", True, True, "0", "100", None, ""),
                MLFunction("1", "", True, True, "0", "180", None, ""),
                MLFunction("3", "", True, True, "", "", ("0", "1", "2"), ""),
                MLFunction("8", "", True, True, "", "", ("0", "1"), ""),
            ),
        )
        coordinator = FakeCoordinator(device)
        coordinator.set_states({"5": "2", "2": "30", "1": "90", "3": "0"})
        runtime = MLConfigEntryRuntime(coordinator, "gateway-1")  # type: ignore[arg-type]
        cover_spec, _jog_open, _jog_close = runtime.registry.describe(device).entities
        cover = MLCoverEntity(runtime, cover_spec)
        self.assertTrue(cover.is_closing)
        self.assertEqual(
            (cover.current_cover_position, cover.current_cover_tilt_position),
            (30, 50),
        )
        self.assertTrue(cover.supported_features & CoverEntityFeature.SET_POSITION)
        self.assertTrue(cover.supported_features & CoverEntityFeature.SET_TILT_POSITION)
        self.assertTrue(cover.supported_features & CoverEntityFeature.OPEN_TILT)
        await cover.async_open_cover()
        await cover.async_set_cover_position(position=70)
        await cover.async_set_cover_tilt_position(tilt_position=25)
        await cover.async_close_cover_tilt()
        self.assertEqual(
            [(command.fun_no, command.fun_val) for command in coordinator.commands],
            [("5", "1"), ("2", "70"), ("1", "45"), ("3", "2")],
        )

    async def test_diagnostics_redacts_host_and_omits_values(self) -> None:
        entry = SimpleNamespace(
            data={
                "host": "192.0.2.10",
                "gateway_id": "gateway-1",
                "port": 8820,
            },
            runtime_data=self.runtime,
        )
        result = await async_get_config_entry_diagnostics(object(), entry)
        self.assertNotEqual(result["config_entry"]["host"], "192.0.2.10")
        self.assertEqual(result["coordinator"]["state_function_counts"], {
            "observed-basic-light": 1
        })
        self.assertNotIn("true", repr(result))


if __name__ == "__main__":
    unittest.main()
