"""Business rules for missing telemetry without a Home Assistant dependency."""

from __future__ import annotations

import unittest

from custom_components.ml_localservice.mapping.models import HAEntitySpec, HAEntityState
from custom_components.ml_localservice.state_fallback import MissingStateFallback


class MissingStateFallbackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = MissingStateFallback()

    def apply(self, platform, state=None, *, points=("switch",), **kwargs):
        spec = HAEntitySpec("device", platform, "entity", None, points)
        if state is None:
            state = HAEntityState("entity", None, False)
        options = dict(enabled=True, synchronized=True, missing=True, context=spec)
        options.update(kwargs)
        return self.policy.apply(spec, state, **options)

    def test_fresh_light_and_switch_default_off_and_report_replaces_default(self):
        for platform in ("light", "switch"):
            with self.subTest(platform=platform):
                initial = self.apply(platform)
                self.assertTrue(initial.available)
                self.assertIs(initial.native_value, False)
                self.assertEqual(initial.attributes["state_source"], "default")
                reported = self.apply(platform, HAEntityState("entity", True, True))
                self.assertIs(reported.native_value, True)
                self.assertEqual(reported.attributes["state_source"], "reported")
                retained = self.apply(platform)
                self.assertIs(retained.native_value, True)
                self.assertEqual(retained.attributes["state_source"], "last_known")

    def test_cover_default_is_in_ha_coordinates_and_respects_capabilities(self):
        for points in (("status", "position"), ("5", "2"), ("day_position",), ("night_position",)):
            state = self.apply("cover", points=points)
            self.assertTrue(state.available)
            self.assertTrue(state.attributes["is_closed"])
            self.assertEqual(state.attributes["current_cover_position"], 0)
            self.assertNotIn("current_cover_tilt_position", state.attributes)
        state = self.apply("cover", points=("status",))
        self.assertTrue(state.attributes["is_closed"])
        self.assertNotIn("current_cover_position", state.attributes)

    def test_cover_keeps_position_without_claiming_historical_motion_is_current(self):
        points = ("status", "position")
        self.apply("cover", HAEntityState("entity", "1", True, {
            "current_cover_position": 73, "is_closed": False, "is_opening": True,
        }), points=points)
        state = self.apply("cover", points=points)
        self.assertEqual(state.attributes["current_cover_position"], 73)
        self.assertNotIn("is_opening", state.attributes)
        self.assertNotIn("is_closing", state.attributes)
        self.assertEqual(state.attributes["state_source"], "last_known")

    def test_measurements_become_unknown_even_after_a_report(self):
        reported = self.apply("sensor", HAEntityState("entity", 22.5, True), points=("temp",))
        self.assertEqual(reported.native_value, 22.5)
        missing = self.apply("sensor", points=("temp",))
        self.assertTrue(missing.available)
        self.assertIsNone(missing.native_value)
        self.assertEqual(missing.attributes["state_source"], "unknown")

    def test_other_entities_never_invent_locked_alarm_mode_or_temperature(self):
        for platform in ("binary_sensor", "lock", "climate", "select", "fan"):
            with self.subTest(platform=platform):
                state = self.apply(platform)
                self.assertTrue(state.available)
                self.assertIsNone(state.native_value)
                self.assertEqual(state.attributes["state_source"], "unknown")

    def test_climate_keeps_reported_mode_and_target_without_stale_measurements(self):
        self.apply("climate", HAEntityState("entity", "heat", True, {
            "target_temperature": 25.0, "current_temperature": 22.5, "current_humidity": 60.0,
        }))
        state = self.apply("climate")
        self.assertEqual(state.native_value, "heat")
        self.assertEqual(state.attributes["target_temperature"], 25.0)
        self.assertNotIn("current_temperature", state.attributes)
        self.assertNotIn("current_humidity", state.attributes)

    def test_disabled_unsynchronized_and_invalid_states_do_not_fall_back(self):
        self.apply("light", HAEntityState("entity", True, True))
        for overrides in ({"enabled": False}, {"synchronized": False}, {"missing": False}):
            state = self.apply("light", **overrides)
            self.assertFalse(state.available)
            self.assertIsNone(state.native_value)
            self.assertNotIn("state_source", state.attributes)
        invalid = self.apply("light", HAEntityState("entity", None, False, {"invalid_ml_value": "bad"}))
        self.assertFalse(invalid.available)
        self.assertEqual(invalid.attributes["invalid_ml_value"], "bad")

    def test_disconnected_reports_do_not_replace_last_known(self):
        self.apply("light", HAEntityState("entity", True, True))
        self.apply("light", HAEntityState("entity", False, True), synchronized=False)
        self.assertTrue(self.apply("light").native_value)

    def test_default_is_not_cached_as_a_real_report_and_reset_clears_history(self):
        self.assertEqual(self.apply("light").attributes["state_source"], "default")
        self.assertEqual(self.apply("light").attributes["state_source"], "default")
        self.apply("light", HAEntityState("entity", True, True))
        self.policy.reset()
        self.assertEqual(self.apply("light").attributes["state_source"], "default")
        self.apply("light", HAEntityState("entity", True, True))
        self.assertEqual(self.apply("light", context="new-schema").attributes["state_source"], "default")

    def test_policies_do_not_share_history_between_gateways_or_entities(self):
        self.apply("light", HAEntityState("entity", True, True))
        other = MissingStateFallback()
        spec = HAEntitySpec("device", "light", "entity", None, ("switch",))
        state = other.apply(spec, HAEntityState("entity", None, False), enabled=True,
                            synchronized=True, missing=True, context=spec)
        self.assertIs(state.native_value, False)
        self.assertEqual(state.attributes["state_source"], "default")


if __name__ == "__main__":
    unittest.main()
