"""Real aiohttp transport test for WebSocket frame fragmentation."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import struct
import unittest
from unittest.mock import AsyncMock, patch

try:
    import aiohttp
except ModuleNotFoundError:  # pragma: no cover - the lightweight local runner
    aiohttp = None  # type: ignore[assignment]

from custom_components.ml_localservice.client import MLAppWSClient


async def _read_websocket_frame(reader: asyncio.StreamReader) -> bytes:
    first, second = await reader.readexactly(2)
    length = second & 0x7F
    if length == 126:
        length = struct.unpack("!H", await reader.readexactly(2))[0]
    elif length == 127:
        length = struct.unpack("!Q", await reader.readexactly(8))[0]
    mask = await reader.readexactly(4) if second & 0x80 else b""
    payload = await reader.readexactly(length)
    if mask:
        payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
    if first & 0x0F == 0x8:
        return b""
    return payload


async def _fragment_server(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter
) -> None:
    request = await reader.readuntil(b"\r\n\r\n")
    key_line = next(
        line for line in request.split(b"\r\n") if line.lower().startswith(b"sec-websocket-key:")
    )
    key = key_line.split(b":", 1)[1].strip()
    accept = base64.b64encode(
        hashlib.sha1(key + b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11").digest()
    )
    writer.write(
        b"HTTP/1.1 101 Switching Protocols\r\n"
        b"Upgrade: websocket\r\n"
        b"Connection: Upgrade\r\n"
        b"Sec-WebSocket-Accept: "
        + accept
        + b"\r\n\r\n"
    )
    await writer.drain()

    registration = json.loads((await _read_websocket_frame(reader)).decode())
    if registration.get("cmd") != "register":
        writer.close()
        return
    response = json.dumps(
        {"cmd": "registerResult", "result": "success", "msg": "ok"},
        separators=(",", ":"),
    ).encode()
    split = len(response) // 2
    # FIN=0 text frame followed by FIN=1 continuation frame. aiohttp must
    # reassemble these into the one TEXT message consumed by the client reader.
    writer.write(bytes((0x01, split)) + response[:split])
    writer.write(bytes((0x80, len(response) - split)) + response[split:])
    await writer.drain()
    try:
        await asyncio.wait_for(_read_websocket_frame(reader), timeout=1.0)
    except (asyncio.IncompleteReadError, TimeoutError):
        pass
    writer.close()
    await writer.wait_closed()


@unittest.skipIf(aiohttp is None, "aiohttp is provided by Home Assistant")
class AiohttpFragmentTests(unittest.IsolatedAsyncioTestCase):
    async def test_fragmented_registration_response_is_reassembled(self) -> None:
        server = await asyncio.start_server(_fragment_server, "127.0.0.1", 0)
        port = int(server.sockets[0].getsockname()[1])
        try:
            async with aiohttp.ClientSession() as session:  # type: ignore[union-attr]
                with (
                    patch("custom_components.ml_localservice.client.APPWSSERVER_PORT", port),
                    patch(
                        "custom_components.ml_localservice.client._async_host_search_result",
                        new=AsyncMock(
                            return_value=("fixture-one-shot-token", "a" * 32)
                        ),
                    ),
                ):
                    client = MLAppWSClient(
                        session,
                        "127.0.0.1",
                        "fixture-gateway",
                        "a" * 32,
                        port=port,
                    )
                    await client.connect()
                    result = await client.register()
                    self.assertTrue(result.success)
                    await client.close()
                self.assertFalse(session.closed)
        finally:
            server.close()
            await server.wait_closed()


if __name__ == "__main__":
    unittest.main()
