"""Static contract tests for the Task 1-2 integration boundary."""

import importlib
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
INTEGRATION = ROOT / "custom_components" / "ml_localservice"


class ManifestTests(unittest.TestCase):
    def test_manifest_declares_the_ml_zeroconf_matcher(self) -> None:
        manifest = json.loads((INTEGRATION / "manifest.json").read_text(encoding="utf-8"))

        self.assertEqual(manifest["domain"], "ml_localservice")
        self.assertTrue(manifest["config_flow"])
        self.assertEqual(
            manifest["zeroconf"],
            [{"type": "_ml-gwconnect._tcp.local.", "properties": {"md": "1"}}],
        )

    def test_translations_define_all_task_1_2_paths(self) -> None:
        strings = json.loads((INTEGRATION / "strings.json").read_text(encoding="utf-8"))
        config = strings["config"]

        self.assertEqual(config["flow_title"], "{name}")
        self.assertIn("user", config["step"])
        self.assertIn("discovery_confirm", config["step"])
        self.assertIn(
            "{name}", config["step"]["discovery_confirm"]["description"]
        )
        for reason in (
            "already_configured",
            "invalid_discovery",
            "invalid_mode",
            "missing_gateway_id",
            "missing_address",
            "cannot_connect",
        ):
            self.assertIn(reason, config["abort"])

    def test_runtime_platform_boundaries_are_present(self) -> None:
        const = importlib.import_module("custom_components.ml_localservice.const")
        self.assertEqual(
            const.PLATFORMS,
            ("light", "switch", "lock", "button", "event", "sensor", "binary_sensor", "climate", "select", "fan", "cover"),
        )
        self.assertEqual(const.RESERVED_PLATFORMS, ())
        for module in (
            "entity.py",
            "platform.py",
            "light.py",
            "switch.py",
            "event.py",
            "sensor.py",
            "binary_sensor.py",
            "climate.py",
            "select.py",
            "fan.py",
            "cover.py",
            "diagnostics.py",
        ):
            self.assertTrue((INTEGRATION / module).is_file(), module)
