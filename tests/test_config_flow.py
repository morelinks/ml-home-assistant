"""mDNS and manual Config Flow path tests."""

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

try:
    from custom_components.ml_localservice.config_flow import MLConfigFlow, _discovery_data, _manual_schema
    from custom_components.ml_localservice.connection import ConnectionTestError
except ModuleNotFoundError:  # pragma: no cover - local stdlib-only check
    MLConfigFlow = None  # type: ignore[assignment,misc]
    _discovery_data = _manual_schema = None  # type: ignore[assignment]
    ConnectionTestError = Exception  # type: ignore[assignment,misc]


def _info(*, host: str = "198.51.100.23", gateway_id: str = "TEST-GATEWAY-0001", md: str = "1") -> SimpleNamespace:
    return SimpleNamespace(
        type="_ml-gwconnect._tcp.local.", host=host, ip_address=host,
        hostname="Morelinks.local", port=10009,
        properties={"md": md, "id": gateway_id, "pv": "1", "sv": "4.1.6-18", "hv": ""},
    )


@unittest.skipIf(MLConfigFlow is None, "Home Assistant dependencies are not installed")
class ConfigFlowTests(unittest.TestCase):
    def _flow(self) -> MLConfigFlow:
        flow = MLConfigFlow()
        flow.hass = object()
        flow.context = {"source": "test"}
        flow.async_set_unique_id = AsyncMock(return_value=None)
        flow.async_show_form = lambda **kwargs: {"type": "form", **kwargs}
        flow.async_abort = lambda **kwargs: {"type": "abort", **kwargs}
        flow.async_create_entry = lambda **kwargs: {"type": "create_entry", **kwargs}
        return flow

    def test_discovery_preserves_identity_versions_and_fixed_port(self) -> None:
        data = _discovery_data(_info(), "TEST-GATEWAY-0001", "198.51.100.23")
        self.assertEqual(data["gateway_id"], "HTG0M0000J0L00EQ")
        self.assertEqual(data["port"], 8820)
        self.assertEqual(data["sv"], "4.1.6-18")

    def test_manual_schema_is_frontend_serializable(self) -> None:
        import voluptuous_serialize
        self.assertEqual(len(voluptuous_serialize.convert(_manual_schema())), 1)

    def test_mdns_discovery_is_visible_before_connection_probe(self) -> None:
        async def run() -> tuple[dict[str, object], dict[str, object]]:
            flow = self._flow()
            connection_test = AsyncMock(side_effect=ConnectionTestError)
            with patch("custom_components.ml_localservice.config_flow.async_test_connection", new=connection_test):
                discovered = await flow.async_step_zeroconf(_info())
                connection_test.assert_not_awaited()
                confirmed = await flow.async_step_discovery_confirm({})
            return discovered, confirmed

        discovered, confirmed = asyncio.run(run())
        self.assertEqual(discovered["step_id"], "discovery_confirm")
        self.assertEqual(confirmed["errors"], {"base": "discovery_address_unreachable"})
        self.assertEqual(confirmed["data_schema"].schema, {})

    def test_manual_configuration_is_a_fixed_port_fallback(self) -> None:
        async def run() -> dict[str, object]:
            flow = self._flow()
            flow._abort_if_unique_id_configured = lambda **kwargs: None
            with patch("custom_components.ml_localservice.config_flow.async_test_connection", new=AsyncMock(return_value="a" * 32)):
                return await flow.async_step_user({"host": "198.51.100.23"})

        result = asyncio.run(run())
        self.assertEqual(result["type"], "create_entry")
        self.assertEqual(result["data"]["port"], 8820)

    def test_invalid_mode_and_missing_data_abort_deterministically(self) -> None:
        async def run() -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
            no_host_info = _info(host="None")
            no_host_info.ip_address = None
            no_host_info.addresses = []
            return (await self._flow().async_step_zeroconf(_info(md="0")), await self._flow().async_step_zeroconf(_info(gateway_id="")), await self._flow().async_step_zeroconf(no_host_info))

        wrong_mode, no_id, no_host = asyncio.run(run())
        self.assertEqual(wrong_mode["reason"], "invalid_mode")
        self.assertEqual(no_id["reason"], "missing_gateway_id")
        self.assertEqual(no_host["reason"], "missing_address")

    def test_mdns_address_change_updates_configured_entry_after_identity_check(self) -> None:
        async def run() -> tuple[dict[str, object], Mock]:
            flow = self._flow()
            entry = SimpleNamespace(title="ML Gateway HTG0M0000J0L00EQ · 192.0.2.10", data={"gateway_id": "HTG0M0000J0L00EQ", "host": "192.0.2.10", "home_fingerprint": "a" * 32})
            update_entry = Mock()
            flow.hass = SimpleNamespace(data={}, config_entries=SimpleNamespace(async_update_entry=update_entry))
            flow.async_set_unique_id = AsyncMock(return_value=entry)
            with patch("custom_components.ml_localservice.config_flow.async_test_connection", new=AsyncMock(return_value="a" * 32)):
                result = await flow.async_step_zeroconf(_info(host="192.0.2.73"))
            return result, update_entry

        result, update_entry = asyncio.run(run())
        self.assertEqual(result["reason"], "already_configured")
        self.assertEqual(update_entry.call_args.kwargs["data"]["host"], "192.0.2.73")

    def test_new_mdns_announcement_replaces_an_obsolete_pending_card(self) -> None:
        async def run() -> Mock:
            flow = self._flow()
            abort = Mock()
            flow.flow_id = "current-flow"
            flow.hass = SimpleNamespace(config_entries=SimpleNamespace(flow=SimpleNamespace(async_progress_by_handler=lambda *args, **kwargs: [{"flow_id": "stale-flow", "context": {"source": "zeroconf", "unique_id": "HTG0M0000J0L00EQ"}}], async_abort=abort)))
            flow.async_step_discovery_confirm = AsyncMock(return_value={"type": "form"})
            await flow.async_step_zeroconf(_info(host="192.0.2.73"))
            return abort

        asyncio.run(run()).assert_called_once_with("stale-flow")

    def test_mdns_confirmation_creates_the_selected_gateway(self) -> None:
        async def run() -> dict[str, object]:
            flow = self._flow()
            flow._abort_if_home_fingerprint_configured = lambda *args, **kwargs: None
            with patch("custom_components.ml_localservice.config_flow.async_test_connection", new=AsyncMock(return_value="a" * 32)):
                await flow.async_step_zeroconf(_info())
                return await flow.async_step_discovery_confirm({})

        result = asyncio.run(run())
        self.assertEqual(result["type"], "create_entry")
        self.assertEqual(result["title"], "TEST-GATEWAY-0001 · 198.51.100.23")
        self.assertEqual(result["data"]["home_fingerprint"], "a" * 32)
