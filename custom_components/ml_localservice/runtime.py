"""Home Assistant Config Entry runtime data for ML LocalService."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from .coordinator import MLClientProtocol, MLCoordinator
from .mapping import MLMappingRegistry, create_default_registry


class MLPlatformRuntime(Protocol):
    """Lifecycle surface owned by one forwarded HA platform."""

    async def async_shutdown(self) -> None: ...


@dataclass(slots=True)
class MLConfigEntryRuntime:
    """Objects owned by one loaded ML Config Entry."""

    coordinator: MLCoordinator
    gateway_id: str
    registry: MLMappingRegistry = field(default_factory=create_default_registry)
    platforms: dict[str, MLPlatformRuntime] = field(default_factory=dict)

    @property
    def client(self) -> MLClientProtocol | None:
        """Expose the coordinator's current client without duplicating ownership."""
        return self.coordinator.client

    async def async_shutdown_platforms(self) -> None:
        """Stop directory listeners and reconciliation tasks for every platform."""
        platforms = tuple(self.platforms.values())
        self.platforms.clear()
        for platform in platforms:
            await platform.async_shutdown()
