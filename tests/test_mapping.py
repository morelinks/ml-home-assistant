"""Pure unit tests for the extensible bidirectional mapping layer."""

from __future__ import annotations

import json
import unittest
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

from custom_components.ml_localservice.mapping import (
    HACommand,
    HADeviceSpec,
    HAEntitySpec,
    HAEntityState,
    MLMappingRegistry,
    AIR_CONDITIONER_TYPE,
    FLOOR_HEAT_TYPE,
    PANEL_AIR_CONDITIONER_TYPE,
    PANEL_CURTAIN_TYPE,
    PANEL_EVENT_TYPE,
    PANEL_FLOOR_HEAT_TYPE,
    PANEL_VENTILATION_TYPE,
    SENSOR_TYPES,
    VENTILATION_TYPES,
    CURTAIN_TYPES,
    UnsupportedHACommandError,
    create_default_registry,
)
from custom_components.ml_localservice.models import (
    MLControlCommand,
    MLDevice,
    MLFunction,
    parse_devices,
)
from custom_components.ml_localservice.mapping.light import color_temp_limits

FIXTURE = Path(__file__).with_name("fixtures") / "mapping_devices.json"


def fixture_devices() -> dict[str, MLDevice]:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return {device.c_dev_id: device for device in parse_devices(payload)}


class SplitMapper:
    """Fixture mapper proving one ML device can emit multiple entities."""

    def map_device(self, device: MLDevice) -> HADeviceSpec:
        return HADeviceSpec(device.c_dev_id, device.name, "virtual")

    def map_entities(self, device: MLDevice) -> tuple[HAEntitySpec, ...]:
        return (
            HAEntitySpec(
                device.c_dev_id, "sensor", "temperature", None, ("temp",)
            ),
            HAEntitySpec(
                device.c_dev_id, "sensor", "humidity", None, ("humidity",)
            ),
        )

    def map_state(
        self, device: MLDevice, states: Mapping[str, str]
    ) -> tuple[HAEntityState, ...]:
        del device
        return (
            HAEntityState("temperature", float(states["temp"]), True),
            HAEntityState("humidity", int(states["humidity"]), True),
        )

    def map_command(
        self, device: MLDevice, entity: HAEntitySpec, command: HACommand
    ) -> tuple[MLControlCommand, ...]:
        del entity, command
        return (
            MLControlCommand(device.c_dev_id, "mode", "auto"),
            MLControlCommand(device.c_dev_id, "target", "22"),
        )


class CombinedMapper(SplitMapper):
    """Fixture mapper proving several functions can compose one entity."""

    def map_entities(self, device: MLDevice) -> tuple[HAEntitySpec, ...]:
        return (
            HAEntitySpec(
                device.c_dev_id,
                "light",
                "combined_light",
                None,
                ("switch", "brightness"),
                ("turn_on", "turn_off", "set_brightness"),
            ),
        )


class MappingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.devices = fixture_devices()
        self.registry = create_default_registry()

    def test_container_placeholder_and_fallback_emit_no_entities(self) -> None:
        container = self.registry.describe(self.devices["relay-container"])
        placeholder = self.registry.describe(self.devices["unknown-do"])

        self.assertEqual(container.device.classification, "container")
        self.assertEqual(placeholder.device.classification, "placeholder")
        self.assertEqual(container.entities, ())
        self.assertEqual(placeholder.entities, ())

        unknown = MLDevice("other", "Other", "99", "unknown", "", ())
        mapped = self.registry.describe(unknown)
        self.assertEqual(mapped.device.classification, "unsupported")
        self.assertIsNone(mapped.mapper_key)
        self.assertEqual(mapped.entities, ())

    def test_observed_basic_light_maps_state_and_both_commands(self) -> None:
        device = self.devices["observed-basic-light"]
        mapped = self.registry.describe(device)
        entity = mapped.entities[0]

        self.assertEqual(mapped.mapper_key, "10/10100001")
        self.assertEqual((entity.platform, entity.mapping_key), ("light", "power"))
        self.assertEqual(entity.consumed_fun_nos, ("switch", "twinkle"))
        self.assertEqual(entity.commands, ("turn_on", "turn_off", "flash"))
        self.assertEqual(
            entity.unique_id("gateway-1"),
            "gateway-1:observed-basic-light:light:power",
        )
        self.assertEqual(
            mapped.device.identifier("gateway-1"),
            ("ml_localservice", "gateway-1:observed-basic-light"),
        )

        mapper = self.registry.resolve(device)
        off = mapper.map_state(device, {"switch": "false"})[0]
        on = mapper.map_state(device, {"switch": "true"})[0]
        self.assertEqual((off.native_value, off.available), (False, True))
        self.assertEqual((on.native_value, on.available), (True, True))
        self.assertEqual(
            mapper.map_command(device, entity, HACommand("turn_on")),
            (MLControlCommand(device.c_dev_id, "switch", "true"),),
        )
        self.assertEqual(
            mapper.map_command(device, entity, HACommand("turn_off")),
            (MLControlCommand(device.c_dev_id, "switch", "false"),),
        )
        self.assertEqual(
            mapper.map_command(device, entity, HACommand("flash", "short")),
            (MLControlCommand(device.c_dev_id, "twinkle", "true"),),
        )
        with self.assertRaises(UnsupportedHACommandError):
            mapper.map_command(device, entity, HACommand("flash", "invalid"))

    def test_missing_or_invalid_capability_is_skipped_safely(self) -> None:
        source = self.devices["observed-basic-light"]
        mapper = self.registry.resolve(source)
        missing = MLDevice(
            source.c_dev_id,
            source.name,
            source.main_type,
            source.sub_type,
            source.room_id,
            (),
        )
        invalid_range = MLDevice(
            source.c_dev_id,
            source.name,
            source.main_type,
            source.sub_type,
            source.room_id,
            (
                MLFunction(
                    "switch", "", True, True, "", "", ("0", "1"), ""
                ),
            ),
        )
        self.assertEqual(mapper.map_entities(missing), ())
        self.assertEqual(mapper.map_entities(invalid_range), ())

        missing_state = mapper.map_state(source, {})[0]
        invalid_state = mapper.map_state(source, {"switch": "1"})[0]
        self.assertFalse(missing_state.available)
        self.assertFalse(invalid_state.available)
        self.assertEqual(invalid_state.attributes["invalid_ml_value"], "1")

    def test_read_only_light_has_state_but_rejects_control(self) -> None:
        source = self.devices["observed-basic-light"]
        function = source.functions[0]
        read_only = MLDevice(
            source.c_dev_id,
            source.name,
            source.main_type,
            source.sub_type,
            source.room_id,
            (
                MLFunction(
                    function.fun_no,
                    function.fun_val,
                    True,
                    False,
                    function.minimum,
                    function.maximum,
                    function.range_values,
                    function.timestamp,
                ),
            ),
        )
        mapper = self.registry.resolve(read_only)
        entity = mapper.map_entities(read_only)[0]
        self.assertEqual(entity.commands, ())
        with self.assertRaises(UnsupportedHACommandError):
            mapper.map_command(read_only, entity, HACommand("turn_on"))

    def test_documented_lighting_categories_map_capabilities_and_commands(self) -> None:
        cases = (
            ("dimmable-light", {"switch": "true", "bright": "50"}, ("switch", "bright"), {"brightness": 128}, "set_brightness", 128, "50"),
            ("color-temp-light", {"switch": "true", "bright": "100", "temp": "4000"}, ("switch", "bright", "temp"), {"brightness": 255, "color_temp_kelvin": 4000}, "set_color_temp", 4000, "4000"),
            ("rgb-light", {"switch": "true", "bright": "50", "rgb": "#12AbF0"}, ("switch", "bright", "rgb"), {"brightness": 128, "rgb_color": (18, 171, 240)}, "set_brightness", 128, "50"),
        )
        for device_id, states, functions, attributes, command_name, command_value, expected_value in cases:
            with self.subTest(device_id=device_id):
                device = self.devices[device_id]
                mapped = self.registry.describe(device)
                entity = mapped.entities[0]
                self.assertEqual(entity.platform, "light")
                self.assertEqual(entity.consumed_fun_nos, functions)
                state = self.registry.resolve(device).map_state(device, states)[0]
                self.assertTrue(state.available)
                self.assertTrue(state.native_value)
                self.assertEqual(dict(state.attributes), attributes)
                command = self.registry.resolve(device).map_command(
                    device, entity, HACommand(command_name, command_value)
                )[0]
                self.assertEqual(command.fun_val, expected_value)

        rgb = self.devices["rgb-light"]
        rgb_entity = self.registry.describe(rgb).entities[0]
        self.assertEqual(
            self.registry.resolve(rgb).map_command(
                rgb, rgb_entity, HACommand("set_rgb_color", (255, 0, 16))
            ),
            (MLControlCommand(rgb.c_dev_id, "rgb", "#FF0010"),),
        )

    def test_color_temperature_clamps_to_advertised_ml_limits(self) -> None:
        source = self.devices["color-temp-light"]
        device = replace(source, functions=tuple(
            replace(fun, minimum="2000", maximum="6000") if fun.fun_no == "temp" else fun
            for fun in source.functions
        ))
        mapper = self.registry.resolve(device)
        entity = mapper.map_entities(device)[0]
        self.assertEqual(color_temp_limits(device), (2000, 6000))
        for requested, expected in ((1800, "2000"), (2000, "2000"), (2221, "2221"),
                                    (2646, "2646"), (3043, "3043"), (6000, "6000"),
                                    (6535, "6000")):
            with self.subTest(requested=requested):
                command, = mapper.map_command(device, entity, HACommand("set_color_temp", requested))
                self.assertEqual(command.fun_val, expected)
                self.assertEqual(command.confirmation_tolerance, 100)
                self.assertEqual(command.to_payload(), {
                    "cDevId": device.c_dev_id, "funNo": "temp", "funVal": expected,
                })
        for invalid in (True, None, "4000", 4000.5):
            with self.subTest(invalid=invalid), self.assertRaises(UnsupportedHACommandError):
                mapper.map_command(device, entity, HACommand("set_color_temp", invalid))
        brightness, = mapper.map_command(device, entity, HACommand("set_brightness", 128))
        self.assertEqual(brightness.confirmation_tolerance, 0)

    def test_color_temperature_defaults_and_invalid_declared_limits(self) -> None:
        source = self.devices["color-temp-light"]
        for lower, upper, expected in (("", "", (2000, 6535)),
                                       ("2000", "6000", (2000, 6000)),
                                       ("3000", "5000", (3000, 5000))):
            device = replace(source, functions=tuple(
                replace(fun, minimum=lower, maximum=upper) if fun.fun_no == "temp" else fun
                for fun in source.functions
            ))
            self.assertEqual(color_temp_limits(device), expected)
        for lower, upper in (("bad", "6000"), ("6000", "2000"), ("0", "6000")):
            device = replace(source, functions=tuple(
                replace(fun, minimum=lower, maximum=upper) if fun.fun_no == "temp" else fun
                for fun in source.functions
            ))
            mapper = self.registry.resolve(device)
            with self.subTest(lower=lower, upper=upper), self.assertRaises(ValueError):
                mapper.map_command(device, mapper.map_entities(device)[0], HACommand("set_color_temp", 4000))

    def test_light_remains_available_when_optional_snapshot_values_are_missing(self) -> None:
        cases = (
            ("color-temp-light", {"switch": "true", "bright": "100"}, {"brightness": 255}),
            ("rgb-light", {"switch": "true", "bright": "50"}, {"brightness": 128}),
            ("rgb-light", {"switch": "true", "bright": "bad", "rgb": "not-rgb"}, {}),
        )
        for device_id, states, attributes in cases:
            with self.subTest(device_id=device_id, states=states):
                state = self.registry.resolve(self.devices[device_id]).map_state(
                    self.devices[device_id], states
                )[0]
                self.assertTrue(state.available)
                self.assertTrue(state.native_value)
                self.assertEqual(dict(state.attributes), attributes)

    def test_documented_lock_and_panel_actions_map_to_platform_entities(self) -> None:
        lock = self.devices["electric-lock"]
        lock_entity = self.registry.describe(lock).entities[0]
        self.assertEqual((lock_entity.platform, lock_entity.commands), ("lock", ("lock", "unlock")))
        self.assertTrue(self.registry.resolve(lock).map_state(lock, {"status": "true"})[0].native_value)
        self.assertEqual(
            self.registry.resolve(lock).map_command(lock, lock_entity, HACommand("unlock")),
            (MLControlCommand(lock.c_dev_id, "status", "false"),),
        )

        panel = self.devices["panel-switch"]
        (panel_event,) = self.registry.describe(panel).entities
        self.assertEqual(
            (panel_event.platform, panel_event.mapping_key),
            ("event", "panel_action"),
        )
        self.assertEqual(
            panel_event.consumed_fun_nos, ("click", "dbclick", "longpress")
        )
        self.assertEqual(panel_event.commands, ())
        self.assertTrue(
            self.registry.resolve(panel).map_state(panel, {})[0].available
        )
        with self.assertRaises(UnsupportedHACommandError):
            self.registry.resolve(panel).map_command(
                panel, panel_event, HACommand("press")
            )

    def test_documented_panel_hvac_and_curtain_types_reuse_ha_capabilities(self) -> None:
        air_conditioner = MLDevice(
            "air-conditioner-panel",
            "Air conditioner panel",
            "15",
            "11500002",
            "",
            (
                self._aircon_function("switch"),
                self._aircon_function("mode"),
                self._aircon_function("settemp"),
                MLFunction(
                    "windspeed", "", True, True, "", "", ("0", "1", "2", "3", "7", "8"), ""
                ),
                self._aircon_function("curtemp", writable=False),
                self._aircon_function("hum", writable=False),
            ),
        )
        self.assertEqual(PANEL_AIR_CONDITIONER_TYPE, "15/11500002")
        aircon_mapped = self.registry.describe(air_conditioner)
        self.assertEqual(aircon_mapped.mapper_key, PANEL_AIR_CONDITIONER_TYPE)
        self.assertEqual(aircon_mapped.device.classification, "air_conditioner_panel")
        aircon = aircon_mapped.entities[0]
        self.assertEqual(
            aircon.consumed_fun_nos,
            ("switch", "mode", "settemp", "windspeed", "curtemp", "hum"),
        )
        self.assertEqual(
            self.registry.resolve(air_conditioner).map_command(
                air_conditioner, aircon, HACommand("set_hvac_mode", "cool")
            ),
            (
                MLControlCommand(air_conditioner.c_dev_id, "mode", "2"),
            ),
        )

        floor_heat = MLDevice(
            "floor-heat-panel",
            "Floor heating panel",
            "15",
            "11500003",
            "",
            (
                MLFunction("switch", "", True, True, "", "", ("true", "false"), ""),
                MLFunction("settemp", "", True, True, "16", "31", None, ""),
                MLFunction("curtemp", "", True, False, "-10", "60", None, ""),
                MLFunction("hum", "", True, False, "0", "100", None, ""),
            ),
        )
        self.assertEqual(PANEL_FLOOR_HEAT_TYPE, "15/11500003")
        floor_mapped = self.registry.describe(floor_heat)
        self.assertEqual(floor_mapped.mapper_key, PANEL_FLOOR_HEAT_TYPE)
        self.assertEqual(floor_mapped.device.classification, "floor_heat_panel")
        (floor_climate,) = floor_mapped.entities
        self.assertEqual(
            floor_climate.consumed_fun_nos,
            ("switch", "settemp", "curtemp", "hum"),
        )
        floor_state = self.registry.resolve(floor_heat).map_state(
            floor_heat,
            {"switch": "true", "settemp": "22", "curtemp": "-5", "hum": "45"},
        )[0]
        self.assertEqual(
            dict(floor_state.attributes)["current_humidity"], 45.0
        )
        self.assertEqual(floor_state.attributes["max_temp"], 31.0)
        self.assertEqual(
            self.registry.resolve(floor_heat).map_state(
                floor_heat, {"switch": "true", "settemp": "40"}
            )[0].attributes["target_temperature"],
            31.0,
        )
        self.assertEqual(
            self.registry.resolve(floor_heat).map_state(
                floor_heat, {"switch": "true", "settemp": "10"}
            )[0].attributes["target_temperature"],
            16.0,
        )
        self.assertEqual(
            self.registry.resolve(floor_heat).map_command(
                floor_heat, floor_climate, HACommand("set_temperature", 36)
            ),
            (MLControlCommand(floor_heat.c_dev_id, "settemp", "31"),),
        )
        self.assertEqual(
            self.registry.resolve(floor_heat).map_command(
                floor_heat, floor_climate, HACommand("set_temperature", 15)
            ),
            (MLControlCommand(floor_heat.c_dev_id, "settemp", "16"),),
        )

        ventilation = MLDevice(
            "ventilation-panel",
            "Ventilation panel",
            "15",
            "11500004",
            "",
            (
                MLFunction("switch", "", True, True, "", "", ("true", "false"), ""),
                MLFunction("windspeed", "", True, True, "", "", ("0", "1", "2"), ""),
            ),
        )
        self.assertEqual(PANEL_VENTILATION_TYPE, "15/11500004")
        ventilation_mapped = self.registry.describe(ventilation)
        self.assertEqual(ventilation_mapped.mapper_key, PANEL_VENTILATION_TYPE)
        self.assertEqual(ventilation_mapped.device.classification, "ventilation_panel")
        (fan,) = ventilation_mapped.entities
        self.assertEqual(fan.platform, "fan")
        self.assertEqual(
            self.registry.resolve(ventilation).map_command(
                ventilation, fan, HACommand("set_preset_mode", "high")
            ),
            (MLControlCommand(ventilation.c_dev_id, "windspeed", "2"),),
        )

        curtain = MLDevice(
            "curtain-panel",
            "Curtain panel",
            "15",
            "11500005",
            "",
            (
                MLFunction("5", "", True, True, "", "", ("0", "1", "2"), ""),
                MLFunction("2", "", True, True, "0", "100", None, ""),
            ),
        )
        self.assertEqual(PANEL_CURTAIN_TYPE, "15/11500005")
        curtain_mapped = self.registry.describe(curtain)
        self.assertEqual(curtain_mapped.mapper_key, PANEL_CURTAIN_TYPE)
        self.assertEqual(curtain_mapped.device.classification, "curtain_panel")
        (cover,) = curtain_mapped.entities
        self.assertEqual((cover.platform, cover.consumed_fun_nos), ("cover", ("5", "2")))
        self.assertEqual(
            self.registry.resolve(curtain).map_command(
                curtain, cover, HACommand("set_cover_position", 65)
            ),
            (MLControlCommand(curtain.c_dev_id, "2", "35"),),
        )

        panel_event = MLDevice(
            "panel-event", "Panel event", "15", "11500001", "", ()
        )
        self.assertEqual(PANEL_EVENT_TYPE, "15/11500001")
        self.assertEqual(self.registry.describe(panel_event).entities, ())

    def test_documented_sensor_categories_map_their_advertised_functions(self) -> None:
        cases = (
            ("3/10300002", (("status", "true", "binary_sensor"), ("sensor_mode", "1", "switch"))),
            ("3/10300003", (("status", "true", "binary_sensor"),)),
            ("3/10300004", (("status", "true", "binary_sensor"),)),
            ("3/10300005", (("status", "true", "binary_sensor"),)),
            ("3/10300006", (("status", "true", "binary_sensor"),)),
            ("3/10300007", (("status", "true", "binary_sensor"),)),
            ("3/10300008", (("status", "true", "binary_sensor"),)),
            ("3/10300009", (("status", "true", "binary_sensor"),)),
            ("3/10300014", (("status", "true", "binary_sensor"), ("lux", "300", "sensor"), ("battery", "3000", "sensor"))),
            ("3/10300015", (("temp", "20", "sensor"),)),
            ("3/10300016", (("hum", "50", "sensor"),)),
            ("3/10300017", (("lux", "300", "sensor"),)),
            ("3/10300019", (("pm25", "12", "sensor"),)),
            ("3/10300020", (("tvoc", "100", "sensor"),)),
            ("3/10300021", (("ppm", "800", "sensor"),)),
            ("3/10300023", (("hpa", "1013", "sensor"),)),
        )
        self.assertEqual(tuple(item[0] for item in cases), SENSOR_TYPES)
        for device_type, points in cases:
            with self.subTest(device_type=device_type):
                main_type, sub_type = device_type.split("/")
                functions = tuple(
                    self._sensor_function(fun_no) for fun_no, _value, _platform in points
                )
                device = MLDevice("sensor", "Sensor", main_type, sub_type, "", functions)
                mapped = self.registry.describe(device)
                self.assertEqual(
                    tuple(entity.platform for entity in mapped.entities),
                    tuple(platform for _fun_no, _value, platform in points),
                )
                states = self.registry.resolve(device).map_state(
                    device, {fun_no: value for fun_no, value, _platform in points}
                )
                self.assertTrue(all(state.available for state in states))

    def test_sensor_mapping_skips_bad_schema_and_rejects_invalid_values(self) -> None:
        device = MLDevice(
            "temperature",
            "Temperature",
            "3",
            "10300015",
            "",
            (MLFunction("temp", "", True, False, "-10", "50", None, ""),),
        )
        mapper = self.registry.resolve(device)
        entity = self.registry.describe(device).entities[0]
        self.assertEqual(
            (entity.platform, entity.device_class, entity.native_unit_of_measurement),
            ("sensor", "temperature", "°C"),
        )
        invalid = mapper.map_state(device, {"temp": "51"})[0]
        self.assertFalse(invalid.available)
        self.assertEqual(invalid.attributes["invalid_ml_value"], "51")

        bad_schema = MLDevice(
            "bad-temperature",
            "Bad temperature",
            "3",
            "10300015",
            "",
            (MLFunction("temp", "", True, False, "60", "70", None, ""),),
        )
        self.assertEqual(self.registry.describe(bad_schema).entities, ())

    def test_person_sensor_mode_translates_to_documented_enum_values(self) -> None:
        device = MLDevice(
            "person",
            "Person detector",
            "3",
            "10300002",
            "",
            (
                self._sensor_function("status"),
                self._sensor_function("sensor_mode"),
            ),
        )
        mapper = self.registry.resolve(device)
        mode = self.registry.describe(device).entities[1]
        self.assertEqual(
            mapper.map_command(device, mode, HACommand("turn_on")),
            (MLControlCommand(device.c_dev_id, "sensor_mode", "1"),),
        )
        self.assertEqual(
            mapper.map_command(device, mode, HACommand("turn_off")),
            (MLControlCommand(device.c_dev_id, "sensor_mode", "0"),),
        )
        with self.assertRaises(UnsupportedHACommandError):
            mapper.map_command(device, self.registry.describe(device).entities[0], HACommand("turn_on"))

    def test_person_detector_emits_occupancy_for_observed_zero_one_status(self) -> None:
        """A missing optional mode must not hide an otherwise readable PIR."""
        device = MLDevice(
            "person-zero-one",
            "Person detector",
            "3",
            "10300002",
            "",
            (
                MLFunction("status", "", True, False, "", "", ("0", "1"), ""),
                MLFunction("workmode", "", True, False, "", "", ("0",), ""),
            ),
        )

        mapped = self.registry.describe(device)
        self.assertEqual(
            tuple((entity.platform, entity.mapping_key) for entity in mapped.entities),
            (("binary_sensor", "occupancy"),),
        )
        mapper = self.registry.resolve(device)
        self.assertEqual(
            mapper.map_state(device, {"status": "0"})[0].native_value,
            False,
        )
        self.assertEqual(
            mapper.map_state(device, {"status": "1"})[0].native_value,
            True,
        )

    def test_documented_air_conditioner_combines_climate_points_and_controls(self) -> None:
        device = MLDevice(
            "air-conditioner",
            "Air conditioner",
            "6",
            "10600001",
            "",
            (
                self._aircon_function("switch"),
                self._aircon_function("mode"),
                self._aircon_function("settemp"),
                self._aircon_function("windspeed"),
                self._aircon_function("curtemp", writable=False),
                self._aircon_function("hum", writable=False),
                self._aircon_function("sweep"),
                self._aircon_function("mute_mode"),
                self._aircon_function("aux_heat"),
            ),
        )
        self.assertEqual(AIR_CONDITIONER_TYPE, "6/10600001")
        mapped = self.registry.describe(device)
        climate, mute, aux_heat = mapped.entities
        self.assertEqual(
            tuple((entity.platform, entity.mapping_key) for entity in mapped.entities),
            (("climate", "climate"), ("switch", "mute_mode"), ("select", "aux_heat")),
        )
        self.assertEqual(
            climate.consumed_fun_nos,
            ("switch", "mode", "settemp", "windspeed", "curtemp", "hum", "sweep"),
        )
        self.assertEqual(
            climate.commands,
            ("turn_on", "turn_off", "set_hvac_mode", "set_temperature", "set_fan_mode", "set_swing_mode"),
        )
        mapper = self.registry.resolve(device)
        states = mapper.map_state(
            device,
            {
                "switch": "true", "mode": "1", "settemp": "24", "windspeed": "2",
                "curtemp": "22.5", "hum": "45", "sweep": "16", "mute_mode": "true",
                "aux_heat": "0",
            },
        )
        self.assertEqual(states[0].native_value, "heat")
        self.assertEqual(
            dict(states[0].attributes),
            {
                "hvac_modes": ("off", "fan_only", "heat", "cool", "auto", "dry"),
                "target_temperature": 24.0, "min_temp": 16.0, "max_temp": 30.0,
                "target_temperature_step": 1.0, "current_temperature": 22.5,
                "current_humidity": 45.0, "fan_mode": "high",
                "fan_modes": ("low", "medium", "high", "auto", "off", "very_low", "medium_low", "medium_high", "very_high", "circulate", "quiet"),
                "swing_mode": "paused", "swing_modes": ("auto", "angle_1", "angle_2", "angle_3", "angle_4", "angle_5", "paused"),
            },
        )
        self.assertTrue(states[1].native_value)
        self.assertEqual(states[2].native_value, "auto")
        self.assertEqual(states[2].attributes["select_options"], ("auto", "on", "off"))
        self.assertEqual(
            mapper.map_command(device, climate, HACommand("set_hvac_mode", "cool")),
            (MLControlCommand(device.c_dev_id, "mode", "2"),),
        )
        self.assertEqual(
            mapper.map_command(device, climate, HACommand("set_hvac_mode", "off")),
            (MLControlCommand(device.c_dev_id, "switch", "false"),),
        )
        self.assertEqual(
            mapper.map_command(device, climate, HACommand("set_temperature", 25)),
            (MLControlCommand(device.c_dev_id, "settemp", "25"),),
        )
        self.assertEqual(
            mapper.map_command(device, climate, HACommand("set_fan_mode", "quiet")),
            (MLControlCommand(device.c_dev_id, "windspeed", "10"),),
        )
        self.assertEqual(
            mapper.map_command(device, climate, HACommand("set_swing_mode", "paused")),
            (MLControlCommand(device.c_dev_id, "sweep", "16"),),
        )
        self.assertEqual(
            mapper.map_command(device, mute, HACommand("turn_off")),
            (MLControlCommand(device.c_dev_id, "mute_mode", "false"),),
        )
        self.assertEqual(
            mapper.map_command(device, aux_heat, HACommand("select_option", "on")),
            (MLControlCommand(device.c_dev_id, "aux_heat", "1"),),
        )

    def test_air_conditioner_skips_missing_main_schema_and_bad_values(self) -> None:
        device = MLDevice(
            "air-conditioner", "Air conditioner", "6", "10600001", "", (
                self._aircon_function("switch"),
                self._aircon_function("settemp"),
            )
        )
        self.assertEqual(self.registry.describe(device).entities, ())

        valid = MLDevice(
            "air-conditioner", "Air conditioner", "6", "10600001", "", (
                self._aircon_function("switch"), self._aircon_function("mode"),
            )
        )
        mapper = self.registry.resolve(valid)
        self.assertFalse(mapper.map_state(valid, {"switch": "true", "mode": "99"})[0].available)
        self.assertFalse(mapper.map_state(valid, {"switch": "invalid", "mode": "1"})[0].available)
        climate = self.registry.describe(valid).entities[0]
        with self.assertRaises(UnsupportedHACommandError):
            mapper.map_command(valid, climate, HACommand("set_temperature", 20))

    def test_air_conditioner_uses_reported_range_and_clamps_target_feedback(self) -> None:
        device = MLDevice(
            "air-conditioner", "Air conditioner", "6", "10600001", "", (
                self._aircon_function("switch"),
                self._aircon_function("mode"),
                MLFunction("settemp", "", True, True, "16", "35", None, ""),
            )
        )
        mapper = self.registry.resolve(device)
        entity = self.registry.describe(device).entities[0]
        state = mapper.map_state(
            device, {"switch": "true", "mode": "1", "settemp": "40"}
        )[0]
        self.assertTrue(state.available)
        self.assertEqual(state.attributes["max_temp"], 35.0)
        self.assertEqual(state.attributes["target_temperature"], 35.0)
        self.assertEqual(
            mapper.map_state(
                device, {"switch": "true", "mode": "1", "settemp": "10"}
            )[0].attributes["target_temperature"],
            16.0,
        )
        self.assertEqual(
            mapper.map_command(device, entity, HACommand("set_temperature", 35)),
            (MLControlCommand(device.c_dev_id, "settemp", "35"),),
        )
        self.assertEqual(
            mapper.map_command(device, entity, HACommand("set_temperature", 36)),
            (MLControlCommand(device.c_dev_id, "settemp", "35"),),
        )
        higher = replace(
            device,
            functions=tuple(
                replace(function, maximum="40")
                if function.fun_no == "settemp" else function
                for function in device.functions
            ),
        )
        higher_state = mapper.map_state(
            higher, {"switch": "true", "mode": "1", "settemp": "40"}
        )[0]
        self.assertEqual(higher_state.attributes["max_temp"], 40.0)
        self.assertEqual(higher_state.attributes["target_temperature"], 40.0)

    def test_climate_temperature_range_falls_back_for_missing_or_invalid_bounds(self) -> None:
        devices = (
            MLDevice(
                "air-conditioner", "Air conditioner", "6", "10600001", "", (
                    self._aircon_function("switch"),
                    self._aircon_function("mode"),
                    MLFunction("settemp", "", True, True, "", "nan", None, ""),
                ),
            ),
            MLDevice(
                "floor-heat-panel", "Floor heating panel", "15", "11500003", "", (
                    MLFunction("switch", "", True, True, "", "", ("true", "false"), ""),
                    MLFunction("settemp", "", True, True, "40", "30", None, ""),
                ),
            ),
        )
        for device in devices:
            with self.subTest(device=device.c_dev_id):
                mapper = self.registry.resolve(device)
                entity = self.registry.describe(device).entities[0]
                state = mapper.map_state(device, {"switch": "true", "mode": "1", "settemp": "40"})[0]
                self.assertEqual(state.attributes["min_temp"], 16.0)
                self.assertEqual(state.attributes["max_temp"], 36.0)
                self.assertEqual(state.attributes["target_temperature"], 36.0)
                self.assertEqual(
                    mapper.map_command(device, entity, HACommand("set_temperature", 40)),
                    (MLControlCommand(device.c_dev_id, "settemp", "36"),),
                )
                with self.assertRaises(UnsupportedHACommandError):
                    mapper.map_command(device, entity, HACommand("set_temperature", float("nan")))

    def test_documented_floor_heat_maps_climate_state_and_commands(self) -> None:
        device = MLDevice(
            "floor-heat",
            "Floor heating",
            "7",
            "10700001",
            "",
            (
                MLFunction("switch", "", True, True, "", "", ("true", "false"), ""),
                MLFunction("settemp", "", True, True, "10", "60", None, ""),
                MLFunction("curtemp", "", True, False, "0", "100", None, ""),
            ),
        )
        self.assertEqual(FLOOR_HEAT_TYPE, "7/10700001")
        mapped = self.registry.describe(device)
        self.assertEqual(mapped.mapper_key, FLOOR_HEAT_TYPE)
        entity = mapped.entities[0]
        self.assertEqual(
            (entity.platform, entity.mapping_key, entity.consumed_fun_nos),
            ("climate", "climate", ("switch", "settemp", "curtemp")),
        )
        self.assertEqual(
            entity.commands,
            ("turn_on", "turn_off", "set_hvac_mode", "set_temperature"),
        )
        mapper = self.registry.resolve(device)
        state = mapper.map_state(
            device, {"switch": "true", "settemp": "24", "curtemp": "22.5"}
        )[0]
        self.assertEqual(state.native_value, "heat")
        self.assertEqual(
            dict(state.attributes),
            {
                "hvac_modes": ("off", "heat"),
                "target_temperature": 24.0,
                "min_temp": 10.0,
                "max_temp": 60.0,
                "target_temperature_step": 1.0,
                "current_temperature": 22.5,
            },
        )
        self.assertEqual(
            mapper.map_command(device, entity, HACommand("set_hvac_mode", "off")),
            (MLControlCommand(device.c_dev_id, "switch", "false"),),
        )
        self.assertEqual(
            mapper.map_command(device, entity, HACommand("set_hvac_mode", "heat")),
            (MLControlCommand(device.c_dev_id, "switch", "true"),),
        )
        self.assertEqual(
            mapper.map_command(device, entity, HACommand("set_temperature", 25)),
            (MLControlCommand(device.c_dev_id, "settemp", "25"),),
        )

    def test_floor_heat_skips_invalid_schema_and_values_safely(self) -> None:
        missing_power = MLDevice(
            "floor-heat", "Floor heating", "7", "10700001", "", (
                MLFunction("settemp", "", True, True, "0", "100", None, ""),
            )
        )
        self.assertEqual(self.registry.describe(missing_power).entities, ())

        device = MLDevice(
            "floor-heat", "Floor heating", "7", "10700001", "", (
                MLFunction("switch", "", True, True, "", "", ("true", "false"), ""),
                MLFunction("settemp", "", True, True, "60", "70", None, ""),
            )
        )
        mapper = self.registry.resolve(device)
        entity = self.registry.describe(device).entities[0]
        self.assertEqual(entity.consumed_fun_nos, ("switch", "settemp"))
        self.assertFalse(mapper.map_state(device, {"switch": "invalid"})[0].available)
        with self.assertRaises(UnsupportedHACommandError):
            mapper.map_command(device, entity, HACommand("set_hvac_mode", "cool"))
        self.assertEqual(
            mapper.map_command(device, entity, HACommand("set_temperature", 50)),
            (MLControlCommand(device.c_dev_id, "settemp", "60"),),
        )

    def test_documented_ventilation_types_map_fan_and_advertised_telemetry(self) -> None:
        full = MLDevice(
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
        self.assertEqual(VENTILATION_TYPES, ("8/10800001", "8/10800004"))
        mapped = self.registry.describe(full)
        self.assertEqual(
            tuple((entity.platform, entity.mapping_key) for entity in mapped.entities),
            (("fan", "fan"), ("sensor", "indoor_temperature"), ("sensor", "indoor_humidity")),
        )
        fan = mapped.entities[0]
        self.assertEqual(fan.consumed_fun_nos, ("switch", "windspeed"))
        self.assertEqual(fan.commands, ("turn_on", "turn_off", "set_preset_mode"))
        mapper = self.registry.resolve(full)
        states = mapper.map_state(
            full,
            {"switch": "true", "windspeed": "2", "intemp": "22.5", "inhum": "45"},
        )
        self.assertEqual((states[0].native_value, dict(states[0].attributes)), (True, {"preset_modes": ("low", "medium", "high"), "preset_mode": "high"}))
        self.assertEqual(
            tuple(state.native_value for state in states[1:]), (22.5, 45.0)
        )
        self.assertEqual(
            mapper.map_command(full, fan, HACommand("set_preset_mode", "medium")),
            (MLControlCommand(full.c_dev_id, "windspeed", "1"),),
        )

        generic = MLDevice(
            "ventilation-basic",
            "Fresh air",
            "8",
            "10800004",
            "",
            full.functions[:2],
        )
        self.assertEqual(
            tuple(entity.platform for entity in self.registry.describe(generic).entities),
            ("fan",),
        )

    def test_ventilation_skips_bad_schema_and_rejects_unsupported_controls(self) -> None:
        missing_power = MLDevice(
            "ventilation", "Fresh air", "8", "10800004", "", (
                MLFunction("windspeed", "", True, True, "", "", ("0", "1", "2"), ""),
            )
        )
        self.assertEqual(self.registry.describe(missing_power).entities, ())

        device = MLDevice(
            "ventilation", "Fresh air", "8", "10800001", "", (
                MLFunction("switch", "", True, True, "", "", ("true", "false"), ""),
                MLFunction("windspeed", "", True, True, "", "", ("3",), ""),
                MLFunction("intemp", "", True, False, "50", "60", None, ""),
            )
        )
        mapper = self.registry.resolve(device)
        fan = self.registry.describe(device).entities[0]
        self.assertEqual((fan.consumed_fun_nos, fan.commands), (("switch",), ("turn_on", "turn_off")))
        self.assertFalse(mapper.map_state(device, {"switch": "invalid"})[0].available)
        with self.assertRaises(UnsupportedHACommandError):
            mapper.map_command(device, fan, HACommand("set_preset_mode", "high"))

    def test_documented_curtains_map_positions_tilt_and_jog_actions(self) -> None:
        basic = MLDevice(
            "curtain", "Curtain", "12", "11200001", "", (
                self._curtain_function("status"), self._curtain_function("position"),
            )
        )
        self.assertEqual(
            CURTAIN_TYPES,
            tuple(f"12/1120000{value}" for value in range(1, 10)),
        )
        mapper = self.registry.resolve(basic)
        basic_cover = self.registry.describe(basic).entities[0]
        self.assertEqual(
            (basic_cover.platform, basic_cover.mapping_key, basic_cover.consumed_fun_nos),
            ("cover", "cover", ("status", "position")),
        )
        self.assertEqual(
            basic_cover.commands,
            ("open_cover", "close_cover", "stop_cover", "set_cover_position"),
        )
        state = mapper.map_state(basic, {"status": "1", "position": "45"})[0]
        self.assertEqual(
            (state.available, dict(state.attributes)),
            (True, {"is_opening": True, "is_closed": False, "current_cover_position": 55}),
        )
        self.assertEqual(
            mapper.map_command(basic, basic_cover, HACommand("close_cover")),
            (MLControlCommand(basic.c_dev_id, "status", "2"),),
        )
        self.assertEqual(
            mapper.map_command(basic, basic_cover, HACommand("set_cover_position", 40)),
            (MLControlCommand(basic.c_dev_id, "position", "60"),),
        )

        angled = MLDevice(
            "angled-curtain", "Angled curtain", "12", "11200007", "", (
                self._curtain_function("status"), self._curtain_function("position"),
                self._curtain_function("angle"), self._curtain_function("control_back_mode"),
                self._curtain_function("jog"),
            )
        )
        angled_entities = self.registry.describe(angled).entities
        cover, jog_open, jog_close = angled_entities
        self.assertEqual(
            tuple((entity.platform, entity.mapping_key) for entity in angled_entities),
            (("cover", "cover"), ("button", "jog_open"), ("button", "jog_close")),
        )
        self.assertEqual(
            cover.commands,
            (
                "open_cover", "close_cover", "stop_cover", "set_cover_position",
                "set_cover_tilt_position", "open_cover_tilt", "close_cover_tilt",
                "stop_cover_tilt",
            ),
        )
        angled_state = self.registry.resolve(angled).map_state(
            angled, {"status": "0", "position": "60", "angle": "90", "control_back_mode": "1"}
        )[0]
        self.assertEqual(
            dict(angled_state.attributes),
            {"current_cover_position": 40, "is_closed": False, "current_cover_tilt_position": 50},
        )
        angled_mapper = self.registry.resolve(angled)
        self.assertEqual(
            angled_mapper.map_command(angled, cover, HACommand("set_cover_tilt_position", 50)),
            (MLControlCommand(angled.c_dev_id, "angle", "90"),),
        )
        self.assertEqual(
            angled_mapper.map_command(angled, cover, HACommand("open_cover_tilt")),
            (MLControlCommand(angled.c_dev_id, "control_back_mode", "1"),),
        )
        self.assertEqual(
            angled_mapper.map_command(angled, jog_open, HACommand("press")),
            (MLControlCommand(angled.c_dev_id, "jog", "0"),),
        )
        self.assertEqual(
            angled_mapper.map_command(angled, jog_close, HACommand("press")),
            (MLControlCommand(angled.c_dev_id, "jog", "1"),),
        )

    def test_day_night_and_travel_free_curtains_keep_separate_capabilities(self) -> None:
        travel_free = MLDevice(
            "travel-free", "Travel-free curtain", "12", "11200003", "", (
                self._curtain_function("status"),
            )
        )
        entity = self.registry.describe(travel_free).entities[0]
        self.assertEqual(entity.consumed_fun_nos, ("status",))
        self.assertNotIn("set_cover_position", entity.commands)

        day_night = MLDevice(
            "day-night", "Day and night curtain", "12", "11200009", "", (
                self._curtain_function("day_status"), self._curtain_function("night_status"),
                self._curtain_function("day_position"), self._curtain_function("night_position"),
            )
        )
        mapped = self.registry.describe(day_night)
        self.assertEqual(
            tuple((entity.mapping_key, entity.name, entity.consumed_fun_nos) for entity in mapped.entities),
            (("day_curtain", "Day curtain", ("day_status", "day_position")), ("night_curtain", "Night curtain", ("night_status", "night_position"))),
        )
        states = self.registry.resolve(day_night).map_state(
            day_night, {"day_status": "1", "night_status": "2", "day_position": "25", "night_position": "75"}
        )
        self.assertEqual(
            tuple(state.attributes["current_cover_position"] for state in states),
            (75, 25),
        )

    def test_default_curtain_inversion_and_explicit_disable(self) -> None:
        device = MLDevice("new-curtain", "Curtain", "12", "11200001", "", (
            self._curtain_function("status"), self._curtain_function("position"),
        ))
        for selected, expected_ha, expected_ml in ((None, 75, "75"), (frozenset(), 25, "25")):
            registry = create_default_registry(inverted_position_device_ids=selected)
            mapper = registry.resolve(device)
            entity, = mapper.map_entities(device)
            state, = mapper.map_state(device, {"status": "0", "position": "25"})
            self.assertEqual(state.attributes["current_cover_position"], expected_ha)
            command, = mapper.map_command(device, entity, HACommand("set_cover_position", 25))
            self.assertEqual(command.fun_val, expected_ml)

    def test_selected_curtain_positions_convert_both_directions_only(self) -> None:
        registry = create_default_registry(
            inverted_position_device_ids=frozenset({"selected"}),
        )
        for category, subtype, positions, statuses in (
            ("12", "11200001", ("position",), ("status",)),
            ("12", "11200007", ("position",), ("status",)),
            ("12", "11200009", ("day_position", "night_position"), ("day_status", "night_status")),
            ("15", "11500005", ("2",), ("5",)),
        ):
            device = MLDevice("selected", "Curtain", category, subtype, "", tuple(
                self._curtain_function(key) for key in (*statuses, *positions)
            ))
            mapper = registry.resolve(device)
            entities = mapper.map_entities(device)
            self.assertEqual(entities, self.registry.describe(device).entities)
            for ml_position, ha_position in ((0, 100), (100, 0), (25, 75)):
                with self.subTest(subtype=subtype, ml_position=ml_position):
                    values = {key: "0" for key in statuses}
                    values.update({key: str(ml_position) for key in positions})
                    states = mapper.map_state(device, values)
                    for entity, state, key in zip(entities, states, positions):
                        self.assertEqual(state.attributes["current_cover_position"], ha_position)
                        self.assertEqual(state.attributes["is_closed"], ha_position == 0)
                        command, = mapper.map_command(device, entity, HACommand("set_cover_position", ha_position))
                        self.assertEqual((command.fun_no, command.fun_val), (key, str(ml_position)))
                        self.assertTrue(command.matches_state(str(ml_position)))
                        self.assertFalse(command.matches_state(str(ha_position)))
                        for action, expected in (("open_cover", "1"), ("close_cover", "2"), ("stop_cover", "0")):
                            action_command, = mapper.map_command(device, entity, HACommand(action))
                            self.assertEqual(action_command.fun_val, expected)
            other = MLDevice("other", device.name, category, subtype, "", device.functions)
            self.assertEqual(
                mapper.map_state(other, {positions[0]: "25"})[0].attributes["current_cover_position"], 25,
            )
            for bad in (None, "bad", "nan", "inf", "-1", "101"):
                values = {key: "0" for key in statuses}
                if bad is not None:
                    values.update({key: bad for key in positions})
                for state in mapper.map_state(device, values):
                    self.assertNotIn("current_cover_position", state.attributes)
                    self.assertNotIn("is_closed", state.attributes)
            for bad in (None, True, "25", -1, 101, float("nan"), float("inf")):
                with self.assertRaises(UnsupportedHACommandError):
                    mapper.map_command(device, entities[0], HACommand("set_cover_position", bad))

    def test_inverted_position_checks_ml_limits_and_preserves_tilt(self) -> None:
        device = MLDevice("selected", "Curtain", "12", "11200008", "", (
            self._curtain_function("status"),
            MLFunction("position", "", True, True, "20", "80", None, ""),
            self._curtain_function("angle_percent"),
        ))
        registry = create_default_registry(inverted_position_device_ids=frozenset({device.c_dev_id}))
        mapper = registry.resolve(device)
        entity, = mapper.map_entities(device)
        # Use an asymmetric range to ensure validation occurs after inversion.
        narrow = MLDevice(device.c_dev_id, device.name, "12", "11200008", "", (
            self._curtain_function("status"),
            MLFunction("position", "", True, True, "20", "60", None, ""),
            self._curtain_function("angle_percent"),
        ))
        command, = mapper.map_command(narrow, entity, HACommand("set_cover_position", 75))
        self.assertEqual(command.fun_val, "25")
        with self.assertRaises(UnsupportedHACommandError):
            mapper.map_command(narrow, entity, HACommand("set_cover_position", 25))
        state, = mapper.map_state(device, {"status": "0", "position": "25", "angle_percent": "25"})
        self.assertEqual(state.attributes["current_cover_position"], 75)
        self.assertEqual(state.attributes["current_cover_tilt_position"], 25)
        tilt, = mapper.map_command(device, entity, HACommand("set_cover_tilt_position", 25))
        self.assertEqual((tilt.fun_no, tilt.fun_val), ("angle_percent", "25"))

    def test_curtains_skip_bad_schema_and_reject_invalid_controls(self) -> None:
        invalid = MLDevice(
            "invalid-curtain", "Invalid curtain", "12", "11200001", "", (
                MLFunction("status", "", True, True, "", "", ("0", "1"), ""),
                MLFunction("position", "", True, True, "101", "120", None, ""),
            )
        )
        self.assertEqual(self.registry.describe(invalid).entities, ())

        valid = MLDevice(
            "valid-curtain", "Valid curtain", "12", "11200008", "", (
                self._curtain_function("status"), self._curtain_function("position"),
                self._curtain_function("angle_percent"), self._curtain_function("control_back_mode"),
            )
        )
        mapper = self.registry.resolve(valid)
        cover = self.registry.describe(valid).entities[0]
        self.assertFalse(mapper.map_state(valid, {"status": "bad", "position": "200"})[0].available)
        with self.assertRaises(UnsupportedHACommandError):
            mapper.map_command(valid, cover, HACommand("set_cover_position", 101))
        with self.assertRaises(UnsupportedHACommandError):
            mapper.map_command(valid, cover, HACommand("set_cover_tilt_position", True))

    @staticmethod
    def _sensor_function(fun_no: str) -> MLFunction:
        ranges = {
            "status": ("true", "false"),
            "sensor_mode": ("0", "1"),
        }
        limits = {
            "lux": ("0", "1200"), "battery": ("0", "3300"),
            "temp": ("-10", "50"), "hum": ("0", "100"),
            "pm25": ("0", "500"), "tvoc": ("0", "4884"),
            "ppm": ("0", "5000"), "hpa": ("0", "2000"),
        }
        minimum, maximum = limits.get(fun_no, ("", ""))
        return MLFunction(
            fun_no, "", True, fun_no == "sensor_mode", minimum, maximum,
            ranges.get(fun_no), "",
        )

    @staticmethod
    def _aircon_function(fun_no: str, *, writable: bool = True) -> MLFunction:
        ranges = {
            "switch": ("true", "false"),
            "mode": tuple(str(value) for value in (0, 1, 2, 3, 6, 7, 16)),
            "windspeed": tuple(str(value) for value in range(11)),
            "sweep": ("0", "1", "2", "3", "4", "5", "16"),
            "mute_mode": ("true", "false"),
            "aux_heat": ("0", "1", "2"),
        }
        limits = {
            "settemp": ("16", "30"), "curtemp": ("-10", "40"), "hum": ("0", "100"),
        }
        minimum, maximum = limits.get(fun_no, ("", ""))
        return MLFunction(fun_no, "", True, writable, minimum, maximum, ranges.get(fun_no), "")

    @staticmethod
    def _curtain_function(fun_no: str, *, writable: bool = True) -> MLFunction:
        ranges = {
            "3": ("0", "1", "2"),
            "5": ("0", "1", "2"),
            "8": ("0", "1"),
            "9": ("0", "1", "2"),
            "10": ("0", "1", "2"),
            "status": ("0", "1", "2"),
            "day_status": ("0", "1", "2"),
            "night_status": ("0", "1", "2"),
            "control_back_mode": ("0", "1", "2"),
            "jog": ("0", "1"),
        }
        limits = {
            "1": ("0", "180"), "2": ("0", "100"),
            "4": ("0", "100"), "11": ("0", "100"), "12": ("0", "100"),
            "position": ("0", "100"), "angle": ("0", "180"),
            "angle_percent": ("0", "100"), "day_position": ("0", "100"),
            "night_position": ("0", "100"),
        }
        minimum, maximum = limits.get(fun_no, ("", ""))
        return MLFunction(fun_no, "", True, writable, minimum, maximum, ranges.get(fun_no), "")

    def test_registry_extension_can_split_entities_and_commands(self) -> None:
        functions = tuple(
            MLFunction(name, "", True, True, "", "", None, "")
            for name in ("temp", "humidity", "mode", "target")
        )
        device = MLDevice("multi", "Multi", "20", "virtual", "", functions)
        registry = MLMappingRegistry()
        mapper = SplitMapper()
        registry.register("virtual", mapper)

        mapped = registry.describe(device)
        self.assertEqual(
            tuple(entity.mapping_key for entity in mapped.entities),
            ("temperature", "humidity"),
        )
        states = mapper.map_state(device, {"temp": "21.5", "humidity": "45"})
        self.assertEqual(
            tuple(state.native_value for state in states), (21.5, 45)
        )
        commands = mapper.map_command(
            device, mapped.entities[0], HACommand("set_target", 22)
        )
        self.assertEqual(tuple(item.fun_no for item in commands), ("mode", "target"))

        with self.assertRaises(ValueError):
            registry.register("virtual", mapper)

    def test_registry_extension_can_combine_multiple_functions(self) -> None:
        functions = tuple(
            MLFunction(name, "", True, True, "", "", None, "")
            for name in ("switch", "brightness")
        )
        device = MLDevice("combined", "Combined", "10", "combined", "", functions)
        registry = MLMappingRegistry()
        registry.register("combined", CombinedMapper())

        entity = registry.describe(device).entities[0]
        self.assertEqual(entity.consumed_fun_nos, ("switch", "brightness"))


if __name__ == "__main__":
    unittest.main()
