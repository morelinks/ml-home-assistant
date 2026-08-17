"""Fake-WebSocket tests for request correlation and client lifecycle."""

from __future__ import annotations

import asyncio
import json
import unittest
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from unittest.mock import AsyncMock, Mock, patch

from custom_components.ml_localservice.client import (
    MLAppWSClient,
    MLAuthenticationError,
    MLCapacityError,
    MLClientState,
    MLConnectionClosedError,
    MLGatewayIdentityMismatchError,
    MLProtocolError,
    MLTimeoutError,
)
from custom_components.ml_localservice.models import MLControlCommand

from test_models import device_response, state_response

HOME_FINGERPRINT = "a" * 32


@dataclass(slots=True)
class FakeMessage:
    type: str
    data: object = None


OnSend = Callable[[dict[str, object]], Awaitable[None]]


class FakeWebSocket:
    def __init__(self, on_send: OnSend | None = None) -> None:
        self.sent: list[dict[str, object]] = []
        self.incoming: asyncio.Queue[FakeMessage] = asyncio.Queue()
        self.closed = False
        self.pongs: list[object] = []
        self.on_send = on_send

    async def send_json(self, payload: dict[str, object]) -> None:
        if self.closed:
            raise OSError("closed")
        copied = dict(payload)
        self.sent.append(copied)
        if self.on_send is not None:
            await self.on_send(copied)

    async def receive(self) -> FakeMessage:
        return await self.incoming.get()

    async def pong(self, data: object) -> None:
        self.pongs.append(data)

    async def close(self) -> None:
        if not self.closed:
            self.closed = True
            await self.incoming.put(FakeMessage("CLOSED"))

    async def feed(self, payload: object) -> None:
        await self.incoming.put(FakeMessage("TEXT", json.dumps(payload)))


class FakeSession:
    def __init__(self, websocket: FakeWebSocket) -> None:
        self.websocket = websocket
        self.closed = False
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def ws_connect(self, url: str, **kwargs: object) -> FakeWebSocket:
        self.calls.append((url, kwargs))
        return self.websocket


class BlockingSession(FakeSession):
    def __init__(self, websocket: FakeWebSocket) -> None:
        super().__init__(websocket)
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def ws_connect(self, url: str, **kwargs: object) -> FakeWebSocket:
        self.calls.append((url, kwargs))
        self.started.set()
        await self.release.wait()
        return self.websocket


def control_result(request_id: str, *, success: bool = True) -> dict[str, object]:
    return {
        "cmd": "ctrlDevResult",
        "result": "success" if success else "fail",
        "msg": "ok" if success else "rejected",
        "reqId": request_id,
        "ts": "1785920352000",
    }


class ClientTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.websocket = FakeWebSocket()
        self.session = FakeSession(self.websocket)
        self.ids = iter(f"request-{number}" for number in range(1, 30))
        self.client = MLAppWSClient(
            self.session,
            "192.0.2.10",
            "fixture-gateway",
            HOME_FINGERPRINT,
            request_timeout=0.05,
            sync_timeout=0.05,
            sync_interval=0,
            sync_retries=0,
            request_id_factory=lambda: next(self.ids),
        )

    async def asyncTearDown(self) -> None:
        await self.client.close()

    async def _connect_and_register(self) -> None:
        async def reply(payload: dict[str, object]) -> None:
            if payload.get("cmd") == "register":
                await self.websocket.feed(
                    {"cmd": "registerResult", "result": "success", "msg": "ok"}
                )

        self.websocket.on_send = reply
        with patch(
            "custom_components.ml_localservice.client._async_host_search_result",
            new=AsyncMock(return_value=("fixture-one-shot-token", HOME_FINGERPRINT)),
        ):
            await self.client.connect()
            result = await self.client.register()
        self.websocket.on_send = None
        self.assertTrue(result.success)
        self.assertTrue(self.client.registered)
        self.assertEqual(self.session.calls[0][0], "ws://192.0.2.10:8820")
        self.assertEqual(self.session.calls[0][1]["heartbeat"], 30.0)
        self.assertEqual(self.session.calls[0][1]["max_msg_size"], 1024 * 1024)
        self.assertNotIn("fixture-one-shot-token", repr(self.client.__dict__))

    async def test_full_sync_timeout_is_independent_from_control_timeout(self) -> None:
        client = MLAppWSClient(
            self.session,
            "192.0.2.10",
            "fixture-gateway",
            HOME_FINGERPRINT,
            request_timeout=0.05,
            sync_timeout=0.1,
        )

        self.assertEqual(client._request_timeout, 0.05)
        self.assertEqual(client._sync_timeout, 0.1)
        await client.close()

    async def test_registered_client_sends_application_heartbeat(self) -> None:
        client = MLAppWSClient(
            self.session,
            "192.0.2.10",
            "heartbeat-gateway",
            HOME_FINGERPRINT,
            request_timeout=0.05,
            sync_timeout=0.05,
            application_heartbeat_interval=0.01,
        )

        async def reply(payload: dict[str, object]) -> None:
            if payload.get("cmd") == "register":
                await self.websocket.feed(
                    {"cmd": "registerResult", "result": "success", "msg": "ok"}
                )

        self.websocket.on_send = reply
        with patch(
            "custom_components.ml_localservice.client._async_host_search_result",
            new=AsyncMock(return_value=("fixture-one-shot-token", HOME_FINGERPRINT)),
        ):
            await client.connect()
            await client.register()

        for _ in range(20):
            if {"cmd": "ping"} in self.websocket.sent:
                break
            await asyncio.sleep(0.01)
        self.assertIn({"cmd": "ping"}, self.websocket.sent)
        await client.close()

    async def test_registration_sync_control_and_notification_return_models(self) -> None:
        await self._connect_and_register()

        async def reply(payload: dict[str, object]) -> None:
            command = payload["cmd"]
            if command == "syncDevList":
                response = device_response()
            elif command == "syncDevStatus":
                response = state_response()
            else:
                response = control_result(str(payload["reqId"]))
            response["reqId"] = payload["reqId"]
            await self.websocket.feed(response)

        self.websocket.on_send = reply
        devices = await self.client.sync_devices()
        states = await self.client.sync_states()
        result = await self.client.control(
            MLControlCommand("fixture-device", "switch", "true")
        )
        await self.websocket.feed(
            {
                "cmd": "devStatusNotify",
                "cDevId": "fixture-device",
                "funNo": "switch",
                "funVal": "true",
                "ts": "1785920353000",
            }
        )
        event = await self.client.next_event(timeout=0.1)

        self.assertEqual(devices[0].functions[0].fun_no, "switch")
        self.assertEqual(states[0].fun_val, "false")
        self.assertTrue(result.success)
        self.assertEqual(event.fun_val, "true")

    async def test_concurrent_control_responses_are_correlated_out_of_order(self) -> None:
        await self._connect_and_register()
        both_sent = asyncio.Event()

        async def record(_payload: dict[str, object]) -> None:
            if len([item for item in self.websocket.sent if item["cmd"] == "ctrlDev"]) == 2:
                both_sent.set()

        self.websocket.on_send = record
        first = asyncio.create_task(
            self.client.control(MLControlCommand("device-a", "switch", "true"))
        )
        second = asyncio.create_task(
            self.client.control(MLControlCommand("device-b", "switch", "false"))
        )
        await asyncio.wait_for(both_sent.wait(), 0.2)
        controls = [item for item in self.websocket.sent if item["cmd"] == "ctrlDev"]
        await self.websocket.feed(control_result(str(controls[1]["reqId"])))
        await self.websocket.feed(control_result(str(controls[0]["reqId"])))

        first_result, second_result = await asyncio.gather(first, second)
        self.assertEqual(first_result.request_id, controls[0]["reqId"])
        self.assertEqual(second_result.request_id, controls[1]["reqId"])
        self.assertEqual(self.client.pending_request_count, 0)

    async def test_notification_does_not_consume_a_request_response_slot(self) -> None:
        await self._connect_and_register()

        async def notify_then_reply(payload: dict[str, object]) -> None:
            await self.websocket.feed(
                {
                    "cmd": "devStatusNotify",
                    "cDevId": "fixture-device",
                    "funNo": "switch",
                    "funVal": "false",
                    "ts": "1785920354000",
                }
            )
            await self.websocket.feed(control_result(str(payload["reqId"])))

        self.websocket.on_send = notify_then_reply
        result = await self.client.control(
            MLControlCommand("fixture-device", "switch", "false")
        )
        event = await self.client.next_event(timeout=0.1)
        self.assertTrue(result.success)
        self.assertEqual(event.fun_val, "false")

    async def test_cancelled_request_cleans_pending_and_late_response_is_ignored(self) -> None:
        await self._connect_and_register()
        sent = asyncio.Event()

        async def observe(payload: dict[str, object]) -> None:
            if payload["cmd"] == "ctrlDev":
                sent.set()

        self.websocket.on_send = observe
        request = asyncio.create_task(
            self.client.control(MLControlCommand("fixture-device", "switch", "true"))
        )
        await asyncio.wait_for(sent.wait(), 0.2)
        request_id = next(
            item["reqId"] for item in self.websocket.sent if item["cmd"] == "ctrlDev"
        )
        request.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await request
        self.assertEqual(self.client.pending_request_count, 0)

        await self.websocket.feed(control_result(str(request_id)))
        await asyncio.sleep(0)
        self.assertEqual(self.client.state, MLClientState.REGISTERED)

    async def test_syncs_are_serialized_but_control_is_not_blocked(self) -> None:
        await self._connect_and_register()
        device_sent = asyncio.Event()
        control_sent = asyncio.Event()

        async def observe(payload: dict[str, object]) -> None:
            if payload["cmd"] == "syncDevList":
                device_sent.set()
            elif payload["cmd"] == "ctrlDev":
                control_sent.set()
                await self.websocket.feed(control_result(str(payload["reqId"])))

        self.websocket.on_send = observe
        devices_task = asyncio.create_task(self.client.sync_devices())
        await asyncio.wait_for(device_sent.wait(), 0.2)
        states_task = asyncio.create_task(self.client.sync_states())
        control_task = asyncio.create_task(
            self.client.control(MLControlCommand("fixture-device", "switch", "true"))
        )
        await asyncio.wait_for(control_sent.wait(), 0.2)
        self.assertFalse(any(item["cmd"] == "syncDevStatus" for item in self.websocket.sent))

        device_request = next(
            item for item in self.websocket.sent if item["cmd"] == "syncDevList"
        )
        response = device_response()
        response["reqId"] = device_request["reqId"]
        await self.websocket.feed(response)
        await devices_task

        for _ in range(20):
            state_requests = [
                item for item in self.websocket.sent if item["cmd"] == "syncDevStatus"
            ]
            if state_requests:
                break
            await asyncio.sleep(0)
        self.assertEqual(len(state_requests), 1)
        state = state_response()
        state["reqId"] = state_requests[0]["reqId"]
        await self.websocket.feed(state)
        await asyncio.gather(states_task, control_task)

    async def test_timeout_has_finite_retry_and_cleans_pending(self) -> None:
        await self._connect_and_register()
        retry_client = MLAppWSClient(
            self.session,
            "192.0.2.10",
            "fixture-gateway",
            HOME_FINGERPRINT,
            request_timeout=0.01,
            sync_timeout=0.01,
            sync_interval=0,
            sync_retries=1,
            request_id_factory=lambda: next(self.ids),
        )
        # Transfer ownership of the connected fake to the dedicated retry client.
        await self.client.close()
        self.websocket = FakeWebSocket()
        self.session = FakeSession(self.websocket)
        retry_client._session = self.session  # isolated fake fixture injection
        self.client = retry_client
        await self._connect_and_register()

        with self.assertRaises(MLTimeoutError):
            await self.client.sync_devices()
        sent = [item for item in self.websocket.sent if item["cmd"] == "syncDevList"]
        self.assertEqual(len(sent), 2)
        self.assertEqual(self.client.pending_request_count, 0)

    async def test_disconnect_fails_pending_and_close_reaps_reader(self) -> None:
        await self._connect_and_register()
        sent = asyncio.Event()

        async def observe(payload: dict[str, object]) -> None:
            if payload["cmd"] == "ctrlDev":
                sent.set()

        self.websocket.on_send = observe
        request = asyncio.create_task(
            self.client.control(MLControlCommand("fixture-device", "switch", "true"))
        )
        await asyncio.wait_for(sent.wait(), 0.2)
        await self.websocket.incoming.put(FakeMessage("CLOSED"))
        with self.assertRaises(MLConnectionClosedError):
            await request
        reader = self.client.reader_task
        self.assertIsNotNone(reader)
        await asyncio.wait_for(reader, 0.2)
        self.assertEqual(self.client.pending_request_count, 0)
        await self.client.close()
        await self.client.close()
        self.assertTrue(reader.done())
        self.assertFalse(self.session.closed)

    async def test_close_racing_with_connect_does_not_leak_a_socket_or_reader(self) -> None:
        websocket = FakeWebSocket()
        session = BlockingSession(websocket)
        client = MLAppWSClient(
            session, "192.0.2.11", "racing-gateway", HOME_FINGERPRINT
        )
        connect = asyncio.create_task(client.connect())
        await asyncio.wait_for(session.started.wait(), 0.2)
        await client.close()
        session.release.set()
        with self.assertRaises(MLConnectionClosedError):
            await connect
        self.assertTrue(websocket.closed)
        self.assertIsNone(client.reader_task)
        self.assertEqual(client.state, MLClientState.CLOSED)

    async def test_ping_and_malformed_event_handling(self) -> None:
        await self._connect_and_register()
        await self.websocket.incoming.put(FakeMessage("PING", b"health"))
        for _ in range(20):
            if self.websocket.pongs:
                break
            await asyncio.sleep(0)
        self.assertEqual(self.websocket.pongs, [b"health"])

        await self.websocket.feed({"cmd": "devStatusNotify", "cDevId": 3})
        with self.assertRaises(MLProtocolError):
            await self.client.next_event(timeout=0.1)

    async def test_registration_rejection_is_classified_and_closes_socket(self) -> None:
        async def reject(payload: dict[str, object]) -> None:
            if payload["cmd"] == "register":
                await self.websocket.feed(
                    {"cmd": "registerResult", "result": "fail", "msg": "error token"}
                )

        self.websocket.on_send = reject
        with patch(
            "custom_components.ml_localservice.client._async_host_search_result",
            new=AsyncMock(return_value=("fixture-one-shot-token", HOME_FINGERPRINT)),
        ):
            await self.client.connect()
            with self.assertRaises(MLAuthenticationError):
                await self.client.register()
        self.assertEqual(self.client.state, MLClientState.CLOSED)
        self.assertTrue(self.websocket.closed)
        self.assertFalse(self.session.closed)

    async def test_identity_mismatch_closes_before_registration_or_sync(self) -> None:
        rediscover = Mock()
        client = MLAppWSClient(
            self.session,
            "192.0.2.10",
            "fixture-gateway",
            HOME_FINGERPRINT,
            identity_mismatch_callback=rediscover,
        )
        with patch(
            "custom_components.ml_localservice.client._async_host_search_result",
            new=AsyncMock(return_value=("foreign-one-shot-token", "b" * 32)),
        ):
            await client.connect()
            with self.assertRaises(MLGatewayIdentityMismatchError):
                await client.register()

        self.assertEqual(self.websocket.sent, [])
        self.assertTrue(self.websocket.closed)
        self.assertEqual(client.state, MLClientState.CLOSED)
        rediscover.assert_called_once()
        await client.close()

    async def test_event_queue_is_bounded(self) -> None:
        await self.client.close()
        self.websocket = FakeWebSocket()
        self.session = FakeSession(self.websocket)
        self.client = MLAppWSClient(
            self.session,
            "192.0.2.10",
            "fixture-gateway",
            HOME_FINGERPRINT,
            event_queue_size=1,
            sync_interval=0,
        )
        await self._connect_and_register()
        event = {
            "cmd": "devStatusNotify",
            "cDevId": "fixture-device",
            "funNo": "switch",
            "funVal": "true",
            "ts": "1785920355000",
        }
        await self.websocket.feed(event)
        await self.websocket.feed(event)
        for _ in range(20):
            if self.client.reader_task and self.client.reader_task.done():
                break
            await asyncio.sleep(0)
        with self.assertRaises(MLCapacityError):
            await self.client.next_event(timeout=0.1)


if __name__ == "__main__":
    unittest.main()
