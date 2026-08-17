"""Diagnostics support for ML LocalService config entries."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import CONF_HOST
from .runtime import MLConfigEntryRuntime


async def async_get_config_entry_diagnostics(
    _hass: HomeAssistant, entry: object
) -> dict[str, Any]:
    """Return bounded metadata without exposing state values or the host."""
    runtime: MLConfigEntryRuntime = entry.runtime_data  # type: ignore[attr-defined]
    data = runtime.coordinator.data
    devices = []
    for device in data.devices.values():
        mapped = runtime.registry.describe(device)
        devices.append(
            {
                "c_dev_id": device.c_dev_id,
                "name": device.name,
                "main_type": device.main_type,
                "sub_type": device.sub_type,
                "classification": mapped.device.classification,
                "functions": [function.fun_no for function in device.functions],
                "entities": [
                    {
                        "platform": spec.platform,
                        "mapping_key": spec.mapping_key,
                        "consumed_functions": list(spec.consumed_fun_nos),
                    }
                    for spec in mapped.entities
                ],
            }
        )
    return async_redact_data(
        {
            "config_entry": dict(entry.data),  # type: ignore[attr-defined]
            "coordinator": {
                "state": runtime.coordinator.state.value,
                "connected": data.connected,
                "registered": data.registered,
                "synchronized": data.synchronized,
                "last_update": data.last_update.isoformat()
                if data.last_update
                else None,
                "background_tasks": len(runtime.coordinator.background_tasks),
                "state_function_counts": {
                    device_id: len(values)
                    for device_id, values in data.states.items()
                },
            },
            "devices": devices,
        },
        {CONF_HOST},
    )
