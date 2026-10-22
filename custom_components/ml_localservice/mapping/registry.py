"""Extensible ML type-key registry with a safe fallback."""

from __future__ import annotations

from ..models import MLDevice
from .base import MLDeviceMapper
from .event import PanelEventMapper
from .climate import AirConditionerMapper
from .container import ContainerMapper, PlaceholderMapper
from .curtain import CurtainMapper
from .floorheat import FloorHeatMapper, PanelFloorHeatMapper
from .fallback import FallbackMapper
from .light import LightMapper
from .lock import LockMapper
from .sensor import SensorMapper
from .ventilation import VentilationMapper
from .models import MappedDevice

RELAY_CONTAINER_TYPE = "1811592189610"
UNKNOWN_DO_TYPE = "4991543809610"
OBSERVED_BASIC_LIGHT_TYPE = "10/10100001"
DIMMABLE_LIGHT_TYPE = "10/10100002"
COLOR_TEMP_LIGHT_TYPE = "10/10100003"
RGB_LIGHT_TYPE = "10/10100004"
ELECTRIC_LOCK_TYPE = "2/10200005"
PANEL_EVENT_TYPE = "15/11500001"
# Retained for callers importing the former registry constant. The type is now
# intentionally mapped to a passive HA event rather than a controllable button.
PANEL_BUTTON_TYPE = PANEL_EVENT_TYPE
PANEL_AIR_CONDITIONER_TYPE = "15/11500002"
PANEL_FLOOR_HEAT_TYPE = "15/11500003"
PANEL_VENTILATION_TYPE = "15/11500004"
PANEL_CURTAIN_TYPE = "15/11500005"
SENSOR_TYPES = (
    "3/10300002",
    "3/10300003",
    "3/10300004",
    "3/10300005",
    "3/10300006",
    "3/10300007",
    "3/10300008",
    "3/10300009",
    "3/10300014",
    "3/10300015",
    "3/10300016",
    "3/10300017",
    "3/10300019",
    "3/10300020",
    "3/10300021",
    "3/10300023",
)
AIR_CONDITIONER_TYPE = "6/10600001"
FLOOR_HEAT_TYPE = "7/10700001"
VENTILATION_TYPES = ("8/10800001", "8/10800004")
CURTAIN_TYPES = tuple(f"12/1120000{value}" for value in range(1, 10))


class MLMappingRegistry:
    """Resolve exact category pairs or stable type IDs to pure mappers."""

    def __init__(self, fallback: MLDeviceMapper | None = None) -> None:
        self._mappers: dict[str, MLDeviceMapper] = {}
        self._fallback = fallback or FallbackMapper()

    def register(self, devtype: str, mapper: MLDeviceMapper) -> None:
        """Register one mapper without silently replacing existing support."""
        if not devtype:
            raise ValueError("devtype must not be empty")
        if devtype in self._mappers:
            raise ValueError(f"mapper already registered for {devtype}")
        self._mappers[devtype] = mapper

    def resolve(self, device: MLDevice) -> MLDeviceMapper:
        """Resolve the most specific available wire-level type key."""
        _, mapper = self._resolve_with_key(device)
        return mapper

    def describe(self, device: MLDevice) -> MappedDevice:
        """Describe all HA objects without importing a client or coordinator."""
        key, mapper = self._resolve_with_key(device)
        entities = mapper.map_entities(device)
        mapping_keys = [entity.mapping_key for entity in entities]
        if len(mapping_keys) != len(set(mapping_keys)):
            raise ValueError("mapper emitted duplicate entity mapping keys")
        if any(entity.c_dev_id != device.c_dev_id for entity in entities):
            raise ValueError("mapper emitted an entity for a different ML device")
        return MappedDevice(
            source=device,
            device=mapper.map_device(device),
            entities=entities,
            mapper_key=key,
        )

    def _resolve_with_key(
        self, device: MLDevice
    ) -> tuple[str | None, MLDeviceMapper]:
        for key in (
            f"{device.main_type}/{device.sub_type}",
            device.sub_type,
            device.main_type,
        ):
            mapper = self._mappers.get(key)
            if mapper is not None:
                return key, mapper
        return None, self._fallback


def create_default_registry(
    *, inverted_position_device_ids: frozenset[str] = frozenset(),
) -> MLMappingRegistry:
    """Create the registry from documented category/device-category pairs."""
    registry = MLMappingRegistry()
    registry.register(RELAY_CONTAINER_TYPE, ContainerMapper())
    registry.register(UNKNOWN_DO_TYPE, PlaceholderMapper())
    registry.register(OBSERVED_BASIC_LIGHT_TYPE, LightMapper(twinkle=True))
    registry.register(DIMMABLE_LIGHT_TYPE, LightMapper(brightness=True))
    registry.register(
        COLOR_TEMP_LIGHT_TYPE, LightMapper(brightness=True, color_temp=True)
    )
    registry.register(RGB_LIGHT_TYPE, LightMapper(brightness=True, rgb=True))
    registry.register(ELECTRIC_LOCK_TYPE, LockMapper())
    registry.register(PANEL_EVENT_TYPE, PanelEventMapper())
    registry.register(
        PANEL_AIR_CONDITIONER_TYPE,
        AirConditionerMapper("air_conditioner_panel"),
    )
    registry.register(PANEL_FLOOR_HEAT_TYPE, PanelFloorHeatMapper())
    registry.register(
        PANEL_VENTILATION_TYPE, VentilationMapper("ventilation_panel")
    )
    registry.register(
        PANEL_CURTAIN_TYPE, CurtainMapper(
            "curtain_panel", numeric_epids=True,
            inverted_position_device_ids=inverted_position_device_ids,
        )
    )
    for device_type in SENSOR_TYPES:
        registry.register(device_type, SensorMapper.for_type(device_type))
    registry.register(AIR_CONDITIONER_TYPE, AirConditionerMapper())
    registry.register(FLOOR_HEAT_TYPE, FloorHeatMapper())
    for device_type in VENTILATION_TYPES:
        registry.register(device_type, VentilationMapper())
    for device_type in CURTAIN_TYPES:
        registry.register(device_type, CurtainMapper(
            inverted_position_device_ids=inverted_position_device_ids,
        ))
    return registry
