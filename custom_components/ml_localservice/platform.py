"""Dynamic platform reconciliation shared by ML entity platforms."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Protocol

from .mapping.models import HAEntitySpec
from .runtime import MLConfigEntryRuntime

if TYPE_CHECKING:
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

_LOGGER = logging.getLogger(__name__)


class MLEntityProtocol(Protocol):
    """Small entity surface required by the reconciler and its unit tests."""

    @property
    def unique_id(self) -> str: ...

    def update_spec(self, spec: HAEntitySpec) -> None: ...

    async def async_remove(self, *, force_remove: bool = False) -> None: ...


EntityFactory = Callable[[MLConfigEntryRuntime, HAEntitySpec], MLEntityProtocol]


class MLPlatformManager:
    """Keep one HA platform aligned with the coordinator device directory."""

    def __init__(
        self,
        hass: object,
        runtime: MLConfigEntryRuntime,
        platform: str,
        async_add_entities: AddEntitiesCallback,
        entity_factory: EntityFactory,
    ) -> None:
        self._hass = hass
        self._runtime = runtime
        self._platform = platform
        self._async_add_entities = async_add_entities
        self._entity_factory = entity_factory
        self._entities: dict[str, MLEntityProtocol] = {}
        self._remove_directory_listener: Callable[[], None] | None = None
        self._tasks: set[asyncio.Task[None]] = set()
        self._lock = asyncio.Lock()
        self._stopping = False

    @property
    def entities(self) -> tuple[MLEntityProtocol, ...]:
        """Expose a stable snapshot for diagnostics and tests."""
        return tuple(self._entities.values())

    async def async_setup(self) -> None:
        """Subscribe before the initial projection so no directory change is lost."""
        self._remove_directory_listener = (
            self._runtime.coordinator.async_add_directory_listener(
                self._directory_changed
            )
        )
        await self.async_reconcile()

    async def async_reconcile(self) -> None:
        """Add, refresh, or retire entities from the latest complete snapshot."""
        async with self._lock:
            if self._stopping:
                return
            desired: dict[str, HAEntitySpec] = {}
            for device in self._runtime.coordinator.data.devices.values():
                try:
                    mapped = self._runtime.registry.describe(device)
                except (TypeError, ValueError):
                    _LOGGER.exception(
                        "Failed to map ML device %s", device.c_dev_id
                    )
                    continue
                for spec in mapped.entities:
                    if spec.platform != self._platform:
                        continue
                    unique_id = spec.unique_id(self._runtime.gateway_id)
                    if unique_id in desired:
                        _LOGGER.error(
                            "Ignoring duplicate ML entity unique ID %s", unique_id
                        )
                        continue
                    desired[unique_id] = spec

            removed_ids = self._entities.keys() - desired.keys()
            for unique_id in tuple(removed_ids):
                entity = self._entities.pop(unique_id)
                # Default removal preserves the Entity Registry record.  If the
                # ML type later returns, the same stable unique ID is restored.
                await entity.async_remove()

            additions: list[MLEntityProtocol] = []
            for unique_id, spec in desired.items():
                entity = self._entities.get(unique_id)
                if entity is not None:
                    entity.update_spec(spec)
                    continue
                entity = self._entity_factory(self._runtime, spec)
                self._entities[unique_id] = entity
                additions.append(entity)
            if additions:
                self._async_add_entities(additions, False)

    async def async_shutdown(self) -> None:
        """Detach directory updates and reap reconciliation work."""
        self._stopping = True
        if self._remove_directory_listener is not None:
            self._remove_directory_listener()
            self._remove_directory_listener = None
        tasks = tuple(self._tasks)
        self._tasks.clear()
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _directory_changed(self, _diff: object) -> None:
        if self._stopping:
            return
        create_task = getattr(self._hass, "async_create_task")
        task = create_task(
            self.async_reconcile(),
            f"ml_localservice_{self._platform}_directory_reconcile",
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)


async def async_setup_ml_platform(
    hass: object,
    entry: object,
    async_add_entities: AddEntitiesCallback,
    platform: str,
    entity_factory: EntityFactory,
) -> None:
    """Set up one forwarded platform using the shared reconciler."""
    runtime: MLConfigEntryRuntime = entry.runtime_data  # type: ignore[attr-defined]
    manager = MLPlatformManager(
        hass, runtime, platform, async_add_entities, entity_factory
    )
    runtime.platforms[platform] = manager
    try:
        await manager.async_setup()
    except BaseException:
        runtime.platforms.pop(platform, None)
        await manager.async_shutdown()
        raise
