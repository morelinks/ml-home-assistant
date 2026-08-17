"""Pure validation tests for the appwsserver protocol models."""

import unittest

from custom_components.ml_localservice.models import (
    MLControlCommand,
    MLModelValidationError,
    parse_devices,
    parse_notification,
    parse_states,
)


def device_response() -> dict[str, object]:
    return {
        "cmd": "syncDevListResult",
        "result": "success",
        "msg": "ok",
        "reqId": "device-1",
        "ts": "1785920346432",
        "devs": [
            {
                "cDevId": "fixture-device",
                "name": "Fixture relay",
                "mainType": "10",
                "subType": "10100001",
                "roomId": "",
                "funs": [
                    {
                        "funNo": "switch",
                        "funVal": "",
                        "r": True,
                        "w": True,
                        "min": "",
                        "max": "",
                        "range": ["true", "false"],
                        "ts": "",
                    }
                ],
            }
        ],
    }


def state_response() -> dict[str, object]:
    return {
        "cmd": "syncDevStatusResult",
        "result": "success",
        "msg": "ok",
        "reqId": "state-1",
        "ts": "1785920350685",
        "devStatus": [
            {
                "cDevId": "fixture-device",
                "status": [{"funNo": "switch", "funVal": "false"}],
            }
        ],
    }


class ModelTests(unittest.TestCase):
    def test_device_and_state_payloads_become_immutable_models(self) -> None:
        devices = parse_devices(device_response())
        states = parse_states(state_response())

        self.assertEqual(devices[0].functions[0].range_values, ("true", "false"))
        self.assertEqual(states[0].c_dev_id, devices[0].c_dev_id)
        self.assertEqual(states[0].timestamp, "1785920350685")
        self.assertNotIsInstance(devices[0], dict)

    def test_notification_is_strongly_validated(self) -> None:
        event = parse_notification(
            {
                "cmd": "devStatusNotify",
                "cDevId": "fixture-device",
                "funNo": "switch",
                "funVal": "true",
                "ts": "1785920351000",
            }
        )
        self.assertEqual((event.fun_no, event.fun_val), ("switch", "true"))

        with self.assertRaises(MLModelValidationError):
            parse_notification({"cmd": "devStatusNotify", "cDevId": 7})

    def test_missing_wrong_overlong_and_duplicate_values_are_rejected(self) -> None:
        missing = device_response()
        del missing["devs"]  # type: ignore[index]
        with self.assertRaises(MLModelValidationError):
            parse_devices(missing)

        wrong_type = state_response()
        wrong_type["devStatus"] = "not-an-array"
        with self.assertRaises(MLModelValidationError):
            parse_states(wrong_type)

        overlong = device_response()
        overlong["devs"][0]["cDevId"] = "x" * 128  # type: ignore[index]
        with self.assertRaises(MLModelValidationError):
            parse_devices(overlong)

        duplicate = device_response()
        duplicate["devs"].append(duplicate["devs"][0])  # type: ignore[union-attr,index]
        with self.assertRaises(MLModelValidationError):
            parse_devices(duplicate)

    def test_control_optional_routing_fields_are_all_or_nothing(self) -> None:
        with self.assertRaises(MLModelValidationError):
            MLControlCommand(
                c_dev_id="fixture-device",
                fun_no="switch",
                fun_val="true",
                inner_fun_no="1",
            )

        command = MLControlCommand(
            c_dev_id="fixture-device",
            fun_no="switch",
            fun_val="true",
            inner_fun_no="1",
            open_device_id="open-device",
            app_device_type_id="type-1",
        )
        self.assertEqual(command.to_payload()["innerFunNo"], "1")


if __name__ == "__main__":
    unittest.main()
