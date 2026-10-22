"""mDNS and manual Config Flow path tests."""

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

try:
    from custom_components.ml_localservice.config_flow import MLConfigFlow, MLOptionsFlow, _discovery_data, _manual_schema
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
        flow._abort_if_unique_id_configured = Mock()
        flow.async_show_form = lambda **kwargs: {"type": "form", **kwargs}
        flow.async_abort = lambda **kwargs: {"type": "abort", **kwargs}
        flow.async_create_entry = lambda **kwargs: {"type": "create_entry", **kwargs}
        return flow

    def test_discovery_preserves_identity_versions_and_fixed_port(self) -> None:
        data = _discovery_data(_info(), "TEST-GATEWAY-0001", "198.51.100.23")
        self.assertEqual(data["gateway_id"], "TEST-GATEWAY-0001")
        self.assertEqual(data["port"], 8820)
        self.assertEqual(data["sv"], "4.1.6-18")

    def test_curtain_options_validate_preserve_and_clear_selection(self) -> None:
        from custom_components.ml_localservice.mapping import create_default_registry
        from custom_components.ml_localservice.models import MLDevice, MLFunction
        device = MLDevice("curtain", "Curtain", "12", "11200001", "", (
            MLFunction("position", "", True, True, "0", "100", None, ""),
        ))
        entry = SimpleNamespace(
            options={"inverted_position_device_ids": ["missing"], "other_option": True},
            runtime_data=SimpleNamespace(
                registry=create_default_registry(),
                coordinator=SimpleNamespace(data=SimpleNamespace(devices={"curtain": device})),
            ),
        )
        flow = MLOptionsFlow()
        flow.handler = "gateway-1"
        flow.hass = SimpleNamespace(config_entries=SimpleNamespace(async_get_known_entry=lambda ident: entry))
        flow.async_show_form = lambda **kwargs: {"type": "form", **kwargs}
        flow.async_create_entry = lambda **kwargs: {"type": "create_entry", **kwargs}

        async def run() -> None:
            form = await flow.async_step_init()
            schema = form["data_schema"]
            from homeassistant.data_entry_flow import FlowResultType
            from homeassistant.helpers.data_entry_flow import FlowManagerIndexView
            serialized = FlowManagerIndexView(None)._prepare_result_json({
                "type": FlowResultType.FORM, "data_schema": schema,
            })
            self.assertEqual(len(serialized["data_schema"]), 1)
            self.assertEqual(schema({})["inverted_position_device_ids"], ["missing"])
            chosen = schema({"inverted_position_device_ids": ["curtain", "missing"]})
            result = await flow.async_step_init(chosen)
            self.assertEqual(result["data"], {
                "inverted_position_device_ids": ["curtain", "missing"], "other_option": True,
            })
            for invalid in (["other-gateway-device"], "curtain", [True]):
                result = await flow.async_step_init({"inverted_position_device_ids": invalid})
                self.assertEqual(result["errors"], {"base": "invalid_selection"})
            result = await flow.async_step_init({"inverted_position_device_ids": []})
            self.assertEqual(result["data"]["inverted_position_device_ids"], [])
            self.assertTrue(result["data"]["other_option"])
        asyncio.run(run())

    def test_manual_schema_is_frontend_serializable(self) -> None:
        from homeassistant.data_entry_flow import FlowResultType
        from homeassistant.helpers.data_entry_flow import FlowManagerIndexView
        # Use HA's actual frontend serializer, including on releases which
        # no longer install voluptuous_serialize.
        result = FlowManagerIndexView(None)._prepare_result_json({
            "type": FlowResultType.FORM, "data_schema": _manual_schema(),
        })
        self.assertEqual(len(result["data_schema"]), 1)

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
            entry = SimpleNamespace(title="ML Gateway TEST-GATEWAY-0001 · 192.0.2.10", data={"gateway_id": "TEST-GATEWAY-0001", "host": "192.0.2.10", "home_fingerprint": "a" * 32})
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
            flow.hass = SimpleNamespace(config_entries=SimpleNamespace(flow=SimpleNamespace(async_progress_by_handler=lambda *args, **kwargs: [{"flow_id": "stale-flow", "context": {"source": "zeroconf", "unique_id": "TEST-GATEWAY-0001"}}], async_abort=abort)))
            flow.async_step_discovery_confirm = AsyncMock(return_value={"type": "form"})
            await flow.async_step_zeroconf(_info(host="192.0.2.73"))
            return abort

        asyncio.run(run()).assert_called_once_with("stale-flow")

    def test_mdns_confirmation_creates_the_selected_gateway(self) -> None:
        async def run() -> dict[str, object]:
            flow = self._flow()
            with patch("custom_components.ml_localservice.config_flow.async_test_connection", new=AsyncMock(return_value="a" * 32)):
                await flow.async_step_zeroconf(_info())
                return await flow.async_step_discovery_confirm({})

        result = asyncio.run(run())
        self.assertEqual(result["type"], "create_entry")
        self.assertEqual(result["title"], "TEST-GATEWAY-0001 · 198.51.100.23")
        self.assertEqual(result["data"]["home_fingerprint"], "a" * 32)

    def _with_entries(self, flow: MLConfigFlow, *entries: object) -> Mock:
        update = Mock()
        flow.hass = SimpleNamespace(
            config_entries=SimpleNamespace(
                async_entries=lambda domain: list(entries),
                async_update_entry=update,
            )
        )
        return update

    def _entry(self, *, gateway_id: str = "OLD-GATEWAY", host: str = "198.51.100.10") -> SimpleNamespace:
        return SimpleNamespace(
            unique_id=gateway_id,
            title=f"ML Gateway {gateway_id} · {host}",
            data={"gateway_id": gateway_id, "host": host, "home_fingerprint": "a" * 32},
        )

    def test_same_home_mdns_gateways_have_independent_entries(self) -> None:
        async def run() -> None:
            old = self._entry()
            original = dict(old.data)
            flow = self._flow()
            update = self._with_entries(flow, old)
            with patch("custom_components.ml_localservice.config_flow.async_test_connection", new=AsyncMock(return_value="a" * 32)):
                await flow.async_step_zeroconf(_info())
                result = await flow.async_step_discovery_confirm({})
            self.assertEqual(result["type"], "create_entry")
            self.assertEqual(result["data"]["gateway_id"], "TEST-GATEWAY-0001")
            self.assertEqual(result["data"]["home_fingerprint"], old.data["home_fingerprint"])
            update.assert_not_called()
            self.assertEqual(old.data, original)

        asyncio.run(run())

    def test_same_home_manual_gateways_have_distinct_endpoint_identities(self) -> None:
        async def run() -> None:
            ids = []
            for host in ("198.51.100.23", "198.51.100.24"):
                flow = self._flow()
                old = self._entry(gateway_id="manual-198.51.100.10")
                update = self._with_entries(flow, old)
                with patch("custom_components.ml_localservice.config_flow.async_test_connection", new=AsyncMock(return_value="a" * 32)):
                    result = await flow.async_step_user({"host": host})
                self.assertEqual(result["type"], "create_entry")
                ids.append(flow.async_set_unique_id.call_args.args[0])
                self.assertEqual(result["data"]["gateway_id"], f"manual-{host}")
                update.assert_not_called()
            self.assertNotEqual(ids[0], ids[1])

        asyncio.run(run())

    def test_manual_input_does_not_replace_a_known_mdns_gateway_identity(self) -> None:
        async def run() -> None:
            entry = self._entry(host="198.51.100.23")
            flow = self._flow()
            update = self._with_entries(flow, entry)
            with patch("custom_components.ml_localservice.config_flow.async_test_connection", new=AsyncMock(return_value="a" * 32)):
                result = await flow.async_step_user({"host": "198.51.100.23"})
            self.assertEqual(result["reason"], "already_configured")
            update.assert_not_called()
            self.assertEqual(entry.data["gateway_id"], "OLD-GATEWAY")

        asyncio.run(run())

    def test_mdns_adopts_manual_endpoint_without_changing_entity_identity(self) -> None:
        async def run() -> None:
            # Cover both new endpoint IDs and legacy Home-based manual IDs.
            for gateway_id in ("manual-198.51.100.23", "udp-" + "a" * 32):
                entry = self._entry(gateway_id=gateway_id, host="198.51.100.23")
                flow = self._flow()
                update = self._with_entries(flow, entry)
                with patch("custom_components.ml_localservice.config_flow.async_test_connection", new=AsyncMock(return_value="a" * 32)):
                    await flow.async_step_zeroconf(_info())
                    result = await flow.async_step_discovery_confirm({})
                self.assertEqual(result["reason"], "already_configured")
                self.assertEqual(update.call_args.kwargs["unique_id"], "TEST-GATEWAY-0001")
                self.assertEqual(update.call_args.kwargs["data"]["gateway_id"], gateway_id)
                self.assertEqual(update.call_args.kwargs["title"], "ML Gateway TEST-GATEWAY-0001 · 198.51.100.23")

        asyncio.run(run())

    def test_mdns_address_change_preserves_adopted_manual_entity_identity(self) -> None:
        async def run() -> None:
            entry = self._entry(gateway_id="manual-198.51.100.10")
            entry.unique_id = "TEST-GATEWAY-0001"
            flow = self._flow()
            update = self._with_entries(flow, entry)
            flow.async_set_unique_id = AsyncMock(return_value=entry)
            with patch("custom_components.ml_localservice.config_flow.async_test_connection", new=AsyncMock(return_value="a" * 32)):
                result = await flow.async_step_zeroconf(_info(host="198.51.100.24"))
            self.assertEqual(result["reason"], "already_configured")
            data = update.call_args.kwargs["data"]
            self.assertEqual(data["gateway_id"], "manual-198.51.100.10")
            self.assertEqual(data["host"], "198.51.100.24")

        asyncio.run(run())

    def test_different_mdns_ids_do_not_merge_even_at_same_home_and_ip(self) -> None:
        async def run() -> None:
            flow = self._flow()
            update = self._with_entries(flow, self._entry(host="198.51.100.23"))
            with patch("custom_components.ml_localservice.config_flow.async_test_connection", new=AsyncMock(return_value="a" * 32)):
                await flow.async_step_zeroconf(_info())
                result = await flow.async_step_discovery_confirm({})
            self.assertEqual(result["type"], "create_entry")
            update.assert_not_called()

        asyncio.run(run())

    def test_manual_alias_requires_matching_verified_home(self) -> None:
        async def run() -> None:
            flow = self._flow()
            entry = self._entry(gateway_id="manual-198.51.100.23", host="198.51.100.23")
            update = self._with_entries(flow, entry)
            with patch("custom_components.ml_localservice.config_flow.async_test_connection", new=AsyncMock(return_value="b" * 32)):
                await flow.async_step_zeroconf(_info())
                result = await flow.async_step_discovery_confirm({})
            self.assertEqual(result["type"], "create_entry")
            update.assert_not_called()

        asyncio.run(run())

    def test_same_gateway_changed_home_does_not_update_endpoint(self) -> None:
        async def run() -> None:
            flow = self._flow()
            entry = self._entry(gateway_id="TEST-GATEWAY-0001")
            update = self._with_entries(flow, entry)
            flow.async_set_unique_id = AsyncMock(return_value=entry)
            with patch("custom_components.ml_localservice.config_flow.async_test_connection", new=AsyncMock(return_value="b" * 32)):
                result = await flow.async_step_zeroconf(_info())
            self.assertEqual(result["reason"], "already_configured")
            update.assert_not_called()

        asyncio.run(run())
