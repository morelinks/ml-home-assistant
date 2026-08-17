"""One-shot appwsserver connectivity and registration check.

This module deliberately contains only the short probe needed by Config Flow.
The persistent WebSocket client belongs to Task 1-3 and must not be created by
each config-flow or entity instance.
"""

from __future__ import annotations

import asyncio
import base64
import hmac
import hashlib
import json
import logging
from collections.abc import Mapping
from typing import Any

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from .const import (
    APP_ID,
    APP_AUTH_AES_KEY,
    APP_AUTH_TOKEN,
    APPWSSERVER_PORT,
    CONNECTION_TIMEOUT,
    HOST_SEARCH_PORT,
    PROTOCOL_VERSION,
)

_LOGGER = logging.getLogger(__name__)


class ConnectionTestError(Exception):
    """Base class for safe, user-facing connection-test failures."""


class ConnectionTestProtocolError(ConnectionTestError):
    """The gateway returned an invalid discovery or registration response."""


class ConnectionTestAuthenticationError(ConnectionTestError):
    """The gateway rejected the one-shot registration check."""


class _UdpReplyProtocol(asyncio.DatagramProtocol):
    """Capture exactly one UDP response without retaining peer data."""

    def __init__(self, result: asyncio.Future[bytes]) -> None:
        self._result = result

    def datagram_received(self, data: bytes, _address: tuple[str, int]) -> None:
        if not self._result.done():
            self._result.set_result(data)

    def error_received(self, exc: Exception) -> None:
        if not self._result.done():
            self._result.set_exception(exc)


def _host_search_payload(dev_id: str) -> bytes:
    """Build the current appwsserver morelinkssearch frame."""
    body = json.dumps(
        {"cmd": "morelinkssearch", "appId": APP_ID, "devId": dev_id, "ver": PROTOCOL_VERSION},
        separators=(",", ":"),
    ).encode("utf-8")
    return b"\x55\x01" + len(body).to_bytes(4, "big") + body + b"\xaa"


def _decrypt_discovery_payload(envelope: Mapping[str, object]) -> Mapping[str, object]:
    """Authenticate and decrypt an appwsserver discovery reply."""

    timestamp = envelope.get("timestamp")
    encrypted = envelope.get("encryptData")
    signature = envelope.get("signature")
    if (
        not isinstance(timestamp, str)
        or not timestamp.isdecimal()
        or len(timestamp) > 32
        or not isinstance(encrypted, str)
        or not encrypted
        or len(encrypted) > 16384
        or not isinstance(signature, str)
        or len(signature) != 40
    ):
        raise ConnectionTestProtocolError("invalid encrypted discovery response")
    expected_signature = hashlib.sha1(
        f"{APP_AUTH_TOKEN}{timestamp}{APP_ID}{encrypted}".encode("utf-8")
    ).hexdigest()
    if not hmac.compare_digest(signature, expected_signature):
        raise ConnectionTestProtocolError("invalid discovery response signature")
    try:
        ciphertext = base64.b64decode(encrypted, validate=True)
        key = base64.b64decode(f"{APP_AUTH_AES_KEY}=", validate=True)
    except (ValueError, UnicodeEncodeError) as err:
        raise ConnectionTestProtocolError("invalid discovery response ciphertext") from err
    if len(ciphertext) < 16 or len(ciphertext) % 16:
        raise ConnectionTestProtocolError("invalid discovery response ciphertext")
    decryptor = Cipher(algorithms.AES(key), modes.CBC(key[:16])).decryptor()
    plaintext = decryptor.update(ciphertext) + decryptor.finalize()
    padding = plaintext[-1]
    if padding == 0 or padding > 16 or plaintext[-padding:] != bytes((padding,)) * padding:
        raise ConnectionTestProtocolError("invalid discovery response padding")
    try:
        payload = json.loads(plaintext[:-padding].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as err:
        raise ConnectionTestProtocolError("invalid discovery response JSON") from err
    if not isinstance(payload, Mapping):
        raise ConnectionTestProtocolError("discovery response is not an object")
    if payload.get("ts") != timestamp:
        raise ConnectionTestProtocolError("discovery response timestamp mismatch")
    return payload


def _host_search_response_payload(data: bytes, *, strict_command: bool) -> Mapping[str, object]:
    """Decode one authenticated morelinkssearch response frame."""

    if len(data) < 7 or data[:2] != b"\x55\x01" or data[-1:] != b"\xaa":
        raise ConnectionTestProtocolError("invalid discovery response frame")

    body_length = int.from_bytes(data[2:6], "big")
    if body_length != len(data) - 7:
        raise ConnectionTestProtocolError("invalid discovery response length")

    try:
        payload = json.loads(data[6:-1].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as err:
        raise ConnectionTestProtocolError("invalid discovery response JSON") from err
    if not isinstance(payload, Mapping):
        raise ConnectionTestProtocolError("discovery response is not an object")
    command = payload.get("cmd")
    if command != "morelinkssearchResult":
        raise ConnectionTestProtocolError("unexpected discovery response command")
    if payload.get("ver") != PROTOCOL_VERSION:
        raise ConnectionTestProtocolError("unsupported discovery response version")
    del strict_command
    return _decrypt_discovery_payload(payload)


def _parse_host_search_response(data: bytes) -> tuple[str, int]:
    """Validate the appwsserver UDP frame and return its token and port."""
    payload = _host_search_response_payload(data, strict_command=False)
    token = payload.get("token")
    port = payload.get("port")
    if not isinstance(token, str) or not token or len(token) > 128:
        raise ConnectionTestProtocolError("hostSearch response has no token")
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ConnectionTestProtocolError("hostSearch response has an invalid port")
    if port != APPWSSERVER_PORT:
        raise ConnectionTestProtocolError("gateway did not advertise appwsserver port 8820")
    return token, port


def _home_fingerprint(home_id: object) -> str:
    """Return a non-reversible entry identity without retaining the raw home ID."""

    if not isinstance(home_id, str) or not home_id or len(home_id) > 256:
        return ""
    return hashlib.sha256(home_id.encode("utf-8")).hexdigest()[:32]


async def _async_host_search_result(
    host: str, dev_id: str, timeout: float
) -> tuple[str, str]:
    """Obtain a one-shot token and an optional non-reversible home identity."""
    loop = asyncio.get_running_loop()
    result: asyncio.Future[bytes] = loop.create_future()
    transport: asyncio.DatagramTransport | None = None
    try:
        transport, _ = await loop.create_datagram_endpoint(
            lambda: _UdpReplyProtocol(result),
            remote_addr=(host, HOST_SEARCH_PORT),
        )
        transport.sendto(_host_search_payload(dev_id))
        data = await asyncio.wait_for(result, timeout=timeout)
    except asyncio.TimeoutError as err:
        raise ConnectionTestError("hostSearch timed out") from err
    except OSError as err:
        # A connected UDP socket reports an ICMP port-unreachable response as
        # ConnectionRefusedError while awaiting recvfrom().  It is an ordinary
        # reachability failure, not an unexpected Config Flow exception.
        raise ConnectionTestError("hostSearch connection failed") from err
    except ConnectionTestError:
        raise
    except Exception as err:  # noqa: BLE001 - normalize resolver/socket errors
        raise ConnectionTestError("hostSearch failed") from err
    finally:
        if transport is not None:
            transport.close()

    # _parse_host_search_response also enforces the fixed TCP 8820 endpoint;
    # the mDNS SRV port is never accepted as a WebSocket destination.
    token, _ = _parse_host_search_response(data)
    payload = _host_search_response_payload(data, strict_command=False)
    return token, _home_fingerprint(payload.get("homeId"))


async def _async_host_search(host: str, dev_id: str, timeout: float) -> str:
    """Obtain a one-shot registration token from UDP 8810."""

    token, _home_identity = await _async_host_search_result(host, dev_id, timeout)
    return token


def _registration_payload(dev_id: str, token: str) -> dict[str, str]:
    """Create the current appwsserver registration message."""
    return {"cmd": "register", "appId": APP_ID, "devId": dev_id, "token": token}


def _parse_registration_response(payload: object) -> None:
    if not isinstance(payload, Mapping) or payload.get("cmd") != "registerResult":
        raise ConnectionTestProtocolError("unexpected registration response")
    if payload.get("result") != "success":
        raise ConnectionTestAuthenticationError("gateway rejected registration")


async def async_test_connection(
    hass: Any,
    *,
    host: str,
    gateway_id: str,
    port: int = APPWSSERVER_PORT,
    timeout: float = CONNECTION_TIMEOUT,
) -> str:
    """Run morelinkssearch and ``register`` against the fixed appwsserver endpoint.

    The token exists only within this call. It is not logged or stored in the
    Config Entry because it is one-shot and must be reacquired by the runtime
    client during normal setup.
    """
    if port != APPWSSERVER_PORT:
        raise ValueError("appwsserver port must be 8820")
    if not host or not gateway_id:
        raise ValueError("host and gateway_id are required")

    try:
        from aiohttp import ClientError, WSMsgType
        from homeassistant.helpers.aiohttp_client import async_get_clientsession
    except ImportError as err:  # pragma: no cover - only possible outside HA
        raise ConnectionTestError("Home Assistant HTTP client is unavailable") from err

    dev_id = f"ha-{gateway_id}"
    token, home_fingerprint = await _async_host_search_result(host, dev_id, timeout)
    session = async_get_clientsession(hass)
    websocket = None
    try:
        websocket = await session.ws_connect(
            f"ws://{host}:{APPWSSERVER_PORT}", timeout=timeout
        )
        await websocket.send_json(_registration_payload(dev_id, token))
        message = await websocket.receive(timeout=timeout)
        if message.type != WSMsgType.TEXT:
            raise ConnectionTestProtocolError("registration response was not text")
        try:
            response = json.loads(message.data)
        except (TypeError, json.JSONDecodeError) as err:
            raise ConnectionTestProtocolError("invalid registration response JSON") from err
        _parse_registration_response(response)
    except ConnectionTestError:
        raise
    except asyncio.TimeoutError as err:
        raise ConnectionTestError("WebSocket connection timed out") from err
    except (OSError, ClientError) as err:
        raise ConnectionTestError("cannot connect to appwsserver") from err
    finally:
        if websocket is not None:
            try:
                await websocket.close()
            except (OSError, ClientError, asyncio.TimeoutError):
                _LOGGER.debug("failed to close one-shot appwsserver probe")
    return home_fingerprint
