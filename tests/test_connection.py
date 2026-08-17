"""Pure tests for appwsserver registration framing and validation."""

import base64
import json
import unittest
from hashlib import sha1

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from custom_components.ml_localservice.connection import ConnectionTestAuthenticationError, ConnectionTestProtocolError, _host_search_payload, _parse_host_search_response, _parse_registration_response
from custom_components.ml_localservice.const import APP_AUTH_AES_KEY, APP_AUTH_TOKEN


def _frame(payload: dict[str, object]) -> bytes:
    body = json.dumps(payload, separators=(",", ":")).encode()
    return b"\x55\x01" + len(body).to_bytes(4, "big") + body + b"\xaa"


def _encrypted_frame(payload: dict[str, object], *, timestamp: str = "1723881600") -> bytes:
    plaintext = json.dumps({**payload, "ts": timestamp}, separators=(",", ":")).encode()
    padding = 16 - len(plaintext) % 16
    key = base64.b64decode(f"{APP_AUTH_AES_KEY}=")
    encryptor = Cipher(algorithms.AES(key), modes.CBC(key[:16])).encryptor()
    ciphertext = encryptor.update(plaintext + bytes((padding,)) * padding)
    encrypted = base64.b64encode(ciphertext + encryptor.finalize()).decode()
    signature = sha1(f"{APP_AUTH_TOKEN}{timestamp}homeassistant{encrypted}".encode()).hexdigest()
    return _frame({"cmd": "morelinkssearchResult", "ver": "1.0.0", "timestamp": timestamp, "encryptData": encrypted, "signature": signature})


class ConnectionProbeTests(unittest.TestCase):
    def test_morelinkssearch_frame_is_length_prefixed(self) -> None:
        frame = _host_search_payload("ha-HTG0M0000J0L00EQ")
        self.assertEqual(frame[:2], b"\x55\x01")
        self.assertEqual(frame[-1:], b"\xaa")
        self.assertEqual(int.from_bytes(frame[2:6], "big"), len(frame) - 7)

    def test_host_search_response_requires_appwsserver_port(self) -> None:
        with self.assertRaises(ConnectionTestProtocolError):
            _parse_host_search_response(_encrypted_frame({"token": "one-shot", "port": 10009}))
        self.assertEqual(_parse_host_search_response(_encrypted_frame({"token": "one-shot", "port": 8820})), ("one-shot", 8820))

    def test_host_search_response_rejects_bad_frame_and_signature(self) -> None:
        with self.assertRaises(ConnectionTestProtocolError):
            _parse_host_search_response(b"not-an-appwsserver-frame")
        response = bytearray(_encrypted_frame({"token": "one-shot", "port": 8820}))
        response[-2] = ord("0") if response[-2] != ord("0") else ord("1")
        with self.assertRaises(ConnectionTestProtocolError):
            _parse_host_search_response(bytes(response))

    def test_registration_result_is_strict(self) -> None:
        _parse_registration_response({"cmd": "registerResult", "result": "success"})
        with self.assertRaises(ConnectionTestAuthenticationError):
            _parse_registration_response({"cmd": "registerResult", "result": "fail"})
        with self.assertRaises(ConnectionTestProtocolError):
            _parse_registration_response({"cmd": "syncDevListResult", "result": "success"})
