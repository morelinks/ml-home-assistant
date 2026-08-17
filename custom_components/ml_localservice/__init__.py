"""Home Assistant integration for ML LocalService gateways."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from .client import MLAppWSClient, MLClientError
from .const import (
    APPWSSERVER_PORT,
    CONF_GATEWAY_ID,
    CONF_HOME_FINGERPRINT,
    CONF_HOST,
    CONF_PORT,
    DOMAIN,
    PLATFORMS,
)
from .coordinator import MLCoordinator
from .runtime import MLConfigEntryRuntime

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

    type MLConfigEntry = ConfigEntry[MLConfigEntryRuntime]
else:
    MLConfigEntry = Any

__all__ = ["DOMAIN"]

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, _config: dict[str, Any]) -> bool:
    """Set up the mDNS-discovered ML LocalService integration."""
    del hass
    return True


async def async_setup_entry(hass: HomeAssistant, entry: MLConfigEntry) -> bool:
    """Create exactly one client/coordinator runtime for this Config Entry."""
    from homeassistant.exceptions import ConfigEntryNotReady
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    expected_home_fingerprint = entry.data.get(CONF_HOME_FINGERPRINT)
    if (
        not isinstance(expected_home_fingerprint, str)
        or len(expected_home_fingerprint) != 32
        or any(character not in "0123456789abcdef" for character in expected_home_fingerprint)
    ):
        raise ConfigEntryNotReady(
            "ML gateway has no verified Home identity; reconfigure it through mDNS"
        )

    session = async_get_clientsession(hass)

    def create_client() -> MLAppWSClient:
        return MLAppWSClient(
            session,
            entry.data[CONF_HOST],
            entry.data[CONF_GATEWAY_ID],
            expected_home_fingerprint,
            port=entry.data.get(CONF_PORT, APPWSSERVER_PORT),
        )

    coordinator = MLCoordinator(create_client)
    runtime = MLConfigEntryRuntime(
        coordinator=coordinator,
        gateway_id=entry.data[CONF_GATEWAY_ID],
    )
    entry.runtime_data = runtime
    try:
        await coordinator.async_start()
    except MLClientError as err:
        await coordinator.async_shutdown()
        raise ConfigEntryNotReady("ML gateway initial synchronization failed") from err
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except BaseException:
        await runtime.async_shutdown_platforms()
        await coordinator.async_shutdown()
        raise

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: MLConfigEntry) -> bool:
    """Stop all runtime work owned by this Config Entry."""
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    await entry.runtime_data.async_shutdown_platforms()
    await entry.runtime_data.coordinator.async_shutdown()
    return True


async def _async_update_listener(hass: HomeAssistant, entry: MLConfigEntry) -> None:
    """Reload the entry when discovery updates its endpoint metadata."""
    await hass.config_entries.async_reload(entry.entry_id)
