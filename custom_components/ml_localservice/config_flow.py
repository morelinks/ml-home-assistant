"""Config Flow for discovering and configuring ML LocalService gateways."""

from __future__ import annotations

import ipaddress
import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from .connection import ConnectionTestError, async_test_connection
from .const import (
    ABORT_CANNOT_CONNECT,
    ABORT_INVALID_MODE,
    ABORT_MISSING_ADDRESS,
    ABORT_MISSING_GATEWAY_ID,
    APPWSSERVER_PORT,
    CONF_GATEWAY_ID,
    CONF_HOME_FINGERPRINT,
    CONF_INVERTED_POSITION_DEVICE_IDS,
    CONF_MISSING_STATE_FALLBACK,
    CONF_HOST,
    CONF_HOSTNAME,
    CONF_HV,
    MDNS_GATEWAY_ID,
    CONF_PORT,
    CONF_PV,
    CONF_SV,
    DOMAIN,
    ZEROCONF_MODE,
    ZEROCONF_TYPE,
)

_LOGGER = logging.getLogger(__name__)


def _text(value: object) -> str:
    """Return a trimmed discovery value, decoding byte TXT values if needed."""
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    return value.strip() if isinstance(value, str) else ""


def _properties(info: ZeroconfServiceInfo) -> Mapping[str, object]:
    properties = getattr(info, "properties", {})
    return properties if isinstance(properties, Mapping) else {}


def _discovery_host(info: ZeroconfServiceInfo) -> str:
    """Select the resolved address supplied by HA Zeroconf."""
    missing = object()
    ip_address = getattr(info, "ip_address", missing)
    if ip_address is not missing and ip_address is not None:
        host = _text(getattr(info, "host", ""))
        if host and host.lower() not in {"none", "null"}:
            return host
    host = _text(getattr(info, "address", ""))
    if host and host.lower() not in {"none", "null"}:
        return host
    addresses = getattr(info, "addresses", ())
    if isinstance(addresses, (tuple, list)):
        for address in addresses:
            normalized = _text(address)
            if normalized:
                return normalized
    return ""


def _discovery_data(info: ZeroconfServiceInfo, gateway_id: str, host: str) -> dict[str, Any]:
    """Build the stable Config Entry payload from mDNS data."""
    props = _properties(info)
    return {
        CONF_GATEWAY_ID: gateway_id,
        CONF_HOST: host,
        CONF_HOSTNAME: _text(getattr(info, "hostname", "")),
        # This is intentionally not info.port: mDNS advertises 10009 for the
        # gwconnect service, while appwsserver is always on TCP 8820.
        CONF_PORT: APPWSSERVER_PORT,
        CONF_PV: _text(props.get(CONF_PV)),
        CONF_SV: _text(props.get(CONF_SV)),
        CONF_HV: _text(props.get(CONF_HV)),
        CONF_HOME_FINGERPRINT: "",
    }


def _entry_title(gateway_id: str, host: str) -> str:
    """Return the customer-facing identity and currently verified endpoint."""

    return f"ML Gateway {gateway_id} · {host}"


def _manual_schema() -> vol.Schema:
    return vol.Schema(
        {
            # Keep this schema serializable for the Home Assistant frontend.
            # async_step_user applies _text() and IPv4 validation below before
            # any connection is made or data is persisted.
            vol.Required(CONF_HOST): str,
        }
    )


class MLConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle mDNS and manual ML gateway configuration."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> MLOptionsFlow:
        """Configure position conventions independently for each gateway."""
        return MLOptionsFlow()

    def __init__(self) -> None:
        self._entry_data: dict[str, Any] = {}
        self._discovery_title: str | None = None

    def _set_discovery_title(self, title: str) -> None:
        """Expose one distinguishable title to HA before user confirmation."""
        self._discovery_title = title
        self.context["title_placeholders"] = {"name": title}

    def _show_discovery_confirmation(
        self,
        *,
        errors: Mapping[str, str] | None = None,
    ) -> ConfigFlowResult:
        """Show an mDNS discovery card."""

        title = self._discovery_title or _entry_title(
            self._entry_data[CONF_GATEWAY_ID], self._entry_data[CONF_HOST]
        )
        return self.async_show_form(
            step_id="discovery_confirm",
            data_schema=vol.Schema({}),
            errors=dict(errors or {}),
            description_placeholders={"name": title},
        )

    def _replace_pending_discovery(self, gateway_id: str) -> None:
        """Replace an obsolete card for the same mDNS gateway.

        Home Assistant keeps discovery forms as independent Config Flows.  A
        fresh mDNS announcement therefore cannot mutate the old form in place;
        aborting that stale form before returning this one makes the UI show the
        current endpoint instead of a misleading historical IP address.
        """

        config_entries = getattr(self.hass, "config_entries", None)
        flow_manager = getattr(config_entries, "flow", None)
        progress_by_handler = getattr(flow_manager, "async_progress_by_handler", None)
        abort = getattr(flow_manager, "async_abort", None)
        if progress_by_handler is None or abort is None:
            return
        current_flow_id = getattr(self, "flow_id", None)
        for progress in progress_by_handler(DOMAIN, include_uninitialized=True):
            if progress.get("flow_id") == current_flow_id:
                continue
            context = progress.get("context")
            if not isinstance(context, Mapping):
                continue
            if (
                context.get("source") == "zeroconf"
                and context.get("unique_id") == gateway_id
            ):
                abort(progress["flow_id"])

    async def _async_update_configured_endpoint(
        self,
        entry: Any,
        candidate_data: Mapping[str, Any],
        *,
        expected_fingerprint: str,
        gateway_id: str,
    ) -> bool:
        """Verify a newly discovered address and persist it when it is trusted."""

        host = _text(candidate_data.get(CONF_HOST))
        if not host:
            return False
        current_host = _text(entry.data.get(CONF_HOST))
        known_fingerprint = _text(entry.data.get(CONF_HOME_FINGERPRINT))
        verified_fingerprint = expected_fingerprint or known_fingerprint
        if host != current_host:
            success, verified_fingerprint = await self._async_test(candidate_data)
            if not success:
                _LOGGER.debug("Ignoring unreachable rediscovered ML endpoint %s", host)
                return False
        if known_fingerprint and verified_fingerprint != known_fingerprint:
            _LOGGER.warning(
                "Ignoring rediscovered ML endpoint %s because its identity does not "
                "match configured gateway %s",
                host,
                gateway_id,
            )
            return False
        if expected_fingerprint and verified_fingerprint != expected_fingerprint:
            _LOGGER.warning(
                "Ignoring rediscovered ML endpoint %s because its identity "
                "changed during verification",
                host,
            )
            return False

        updated_data = dict(entry.data)
        updated_data.update(candidate_data)
        # Entity IDs use this key. Keep it stable when mDNS has identified a
        # gateway that was originally configured by its manual endpoint.
        updated_data[CONF_GATEWAY_ID] = entry.data.get(CONF_GATEWAY_ID, gateway_id)
        if verified_fingerprint:
            updated_data[CONF_HOME_FINGERPRINT] = verified_fingerprint
        title = _entry_title(gateway_id, host)
        if updated_data == entry.data and title == entry.title:
            return True
        self.hass.config_entries.async_update_entry(
            entry, data=updated_data, title=title
        )
        return True

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle manual gateway configuration."""
        errors: dict[str, str] = {}
        if user_input is not None:
            host = _text(user_input.get(CONF_HOST))
            try:
                is_ipv4 = ipaddress.ip_address(host).version == 4
            except ValueError:
                is_ipv4 = False
            if not is_ipv4:
                errors[CONF_HOST] = "invalid_host"
            else:
                data = {
                    # UDP registration identifies the Home, not its individual
                    # gateway. Use the explicit endpoint until mDNS supplies
                    # the physical gateway ID; never key entries by Home alone.
                    CONF_GATEWAY_ID: f"manual-{host}",
                    CONF_HOST: host,
                    CONF_HOSTNAME: "",
                    CONF_PORT: APPWSSERVER_PORT,
                    CONF_PV: "",
                    CONF_SV: "",
                    CONF_HV: "",
                    CONF_HOME_FINGERPRINT: "",
                }
                success, home_fingerprint = await self._async_test(data)
                if success and len(home_fingerprint) == 32:
                    gateway_id = data[CONF_GATEWAY_ID]
                    data[CONF_HOME_FINGERPRINT] = home_fingerprint
                    existing = self._abort_if_endpoint_configured(data)
                    if existing is not None:
                        return existing
                    await self.async_set_unique_id(gateway_id)
                    self._abort_if_unique_id_configured()
                    return self.async_create_entry(
                        title=_entry_title(gateway_id, host),
                        data=data,
                    )
                errors["base"] = ABORT_CANNOT_CONNECT

        return self.async_show_form(
            step_id="user",
            data_schema=_manual_schema(),
            errors=errors,
        )

    async def async_step_zeroconf(
        self, discovery_info: ZeroconfServiceInfo
    ) -> ConfigFlowResult:
        """Handle an ML gateway announced through Zeroconf."""
        service_type = _text(getattr(discovery_info, "type", ""))
        if service_type and service_type != ZEROCONF_TYPE:
            return self.async_abort(reason="invalid_discovery")

        properties = _properties(discovery_info)
        if _text(properties.get("md")) != ZEROCONF_MODE:
            return self.async_abort(reason=ABORT_INVALID_MODE)

        gateway_id = _text(properties.get(MDNS_GATEWAY_ID))
        if not gateway_id:
            return self.async_abort(reason=ABORT_MISSING_GATEWAY_ID)

        host = _discovery_host(discovery_info)
        if not host:
            return self.async_abort(reason=ABORT_MISSING_ADDRESS)

        data = _discovery_data(discovery_info, gateway_id, host)
        existing = await self.async_set_unique_id(gateway_id, raise_on_progress=False)
        if existing is not None:
            # Do not trust an address change solely because a service announced
            # the same ID.  A one-shot registration also gives us the stable
            # Home ID fingerprint before the existing entry is moved.
            await self._async_update_configured_endpoint(
                existing,
                data,
                expected_fingerprint="",
                gateway_id=gateway_id,
            )
            return self.async_abort(reason="already_configured")

        # A discovery card is immutable once displayed. Replace an older card
        # for this same stable ID so the frontend shows the newest mDNS IP.
        self._replace_pending_discovery(gateway_id)

        self._entry_data = data
        # The integration name is already shown below HA's discovery title.
        # Put the useful identity first so it remains visible on narrow cards.
        # Connectivity is tested only after confirmation; otherwise a valid
        # mDNS service with a temporarily unavailable appwsserver disappears
        # from the UI without giving the user an actionable error.
        self._set_discovery_title(f"{gateway_id} · {host}")
        return await self.async_step_discovery_confirm()

    async def async_step_discovery_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask the user to confirm a successfully probed mDNS discovery."""
        if user_input is None:
            return self._show_discovery_confirmation()
        expected_fingerprint = _text(
            self._entry_data.get(CONF_HOME_FINGERPRINT)
        )
        success, verified_fingerprint = await self._async_test(self._entry_data)
        if not success or (
            expected_fingerprint and verified_fingerprint != expected_fingerprint
        ):
            return self._show_discovery_confirmation(
                errors={"base": "discovery_address_unreachable"},
            )
        if not expected_fingerprint:
            self._entry_data[CONF_HOME_FINGERPRINT] = verified_fingerprint
        self._abort_if_unique_id_configured()
        existing = self._abort_if_endpoint_configured(
            self._entry_data, discovered=True
        )
        if existing is not None:
            return existing

        return self.async_create_entry(
            title=self._discovery_title
            or _entry_title(
                self._entry_data[CONF_GATEWAY_ID], self._entry_data[CONF_HOST]
            ),
            data=self._entry_data,
        )

    def _abort_if_endpoint_configured(
        self, data: Mapping[str, Any], *, discovered: bool = False
    ) -> ConfigFlowResult | None:
        """Match manual/mDNS aliases only at the same verified endpoint.

        A Home can contain several gateways. Its fingerprint verifies trust,
        but cannot identify which gateway owns a Config Entry or its entities.
        """
        fingerprint = _text(data.get(CONF_HOME_FINGERPRINT))
        host = _text(data.get(CONF_HOST))
        config_entries = getattr(self.hass, "config_entries", None)
        entries = getattr(config_entries, "async_entries", None)
        update_entry = getattr(config_entries, "async_update_entry", None)
        if not fingerprint or not host or entries is None or update_entry is None:
            return None
        for entry in entries(DOMAIN):
            if (
                _text(entry.data.get(CONF_HOST)) != host
                or _text(entry.data.get(CONF_HOME_FINGERPRINT)) != fingerprint
            ):
                continue
            if not discovered:
                return self.async_abort(reason="already_configured")
            gateway_id = data[CONF_GATEWAY_ID]
            entry_id = _text(entry.unique_id)
            if (
                entry_id
                and not entry_id.startswith(("manual-", "udp-"))
                and entry_id != gateway_id
            ):
                # Even a reused IP/Home is not evidence that two distinct
                # physical gateway IDs are interchangeable.
                continue
            updated_data = dict(entry.data)
            updated_data.update(data)
            updated_data[CONF_GATEWAY_ID] = entry.data[CONF_GATEWAY_ID]
            update_entry(
                entry,
                data=updated_data,
                title=_entry_title(gateway_id, host),
                unique_id=gateway_id,
            )
            return self.async_abort(reason="already_configured")
        return None

    async def _async_test(self, data: Mapping[str, Any]) -> tuple[bool, str]:
        """Run the probe and retain only its non-reversible home identity."""
        try:
            home_fingerprint = await async_test_connection(
                self.hass,
                host=data[CONF_HOST],
                gateway_id=data[CONF_GATEWAY_ID],
                port=APPWSSERVER_PORT,
            )
        except ConnectionTestError:
            return False, ""
        except Exception:  # noqa: BLE001 - config flow must not crash on malformed peers
            _LOGGER.exception(
                "Unexpected ML gateway connection-test failure for gateway %s",
                data.get(CONF_GATEWAY_ID, "unknown"),
            )
            return False, ""
        return True, home_fingerprint if isinstance(home_fingerprint, str) else ""


class MLOptionsFlow(OptionsFlow):
    """Configure missing-state handling and curtain position conventions."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        current = self.config_entry.options
        selected = current.get(CONF_INVERTED_POSITION_DEVICE_IDS, [])
        # Retain saved selections when a device is temporarily missing.
        choices = {device_id: device_id for device_id in selected}
        runtime = getattr(self.config_entry, "runtime_data", None)
        if runtime is not None:
            for device in runtime.coordinator.data.devices.values():
                entities = runtime.registry.describe(device).entities
                if any(
                    entity.platform == "cover" and set(entity.consumed_fun_nos).intersection(
                        {"position", "day_position", "night_position", "2"}
                    )
                    for entity in entities
                ):
                    choices[device.c_dev_id] = f"{device.name} ({device.c_dev_id})"
        if CONF_INVERTED_POSITION_DEVICE_IDS not in current:
            # None means all devices, including ones discovered in the future.
            # Saving this form creates an explicit selection, including [].
            selected = sorted(choices)
        errors: dict[str, str] = {}
        if user_input is not None:
            values = user_input.get(CONF_INVERTED_POSITION_DEVICE_IDS, selected)
            fallback = user_input.get(
                CONF_MISSING_STATE_FALLBACK,
                current.get(CONF_MISSING_STATE_FALLBACK, True),
            )
            if not isinstance(fallback, bool):
                errors["base"] = "invalid_fallback"
            elif (
                isinstance(values, list)
                and all(isinstance(value, str) and value in choices for value in values)
            ):
                return self.async_create_entry(
                    title="",
                    data={
                        **current,
                        CONF_INVERTED_POSITION_DEVICE_IDS: sorted(set(values)),
                        CONF_MISSING_STATE_FALLBACK: fallback,
                    },
                )
            else:
                errors["base"] = "invalid_selection"
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Optional(
                    CONF_MISSING_STATE_FALLBACK,
                    default=current.get(CONF_MISSING_STATE_FALLBACK, True),
                ): selector.BooleanSelector(),
                vol.Optional(CONF_INVERTED_POSITION_DEVICE_IDS, default=selected):
                    selector.SelectSelector(selector.SelectSelectorConfig(
                        options=[
                            {"value": key, "label": label}
                            for key, label in sorted(choices.items())
                        ],
                        multiple=True,
                    )),
            }),
            errors=errors,
        )
