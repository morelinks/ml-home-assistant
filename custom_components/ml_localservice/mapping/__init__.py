"""Public pure-logic mapping API for ML LocalService devices."""

from .base import MLDeviceMapper, MLMappingError, UnsupportedHACommandError
from .climate import AirConditionerMapper
from .container import ContainerMapper, PlaceholderMapper
from .curtain import CurtainMapper
from .fallback import FallbackMapper
from .floorheat import FloorHeatMapper, PanelFloorHeatMapper
from .event import PanelEventMapper
from .light import LightMapper
from .lock import LockMapper
from .sensor import SensorMapper
from .ventilation import VentilationMapper
from .models import (
    HACommand,
    HADeviceSpec,
    HAEntitySpec,
    HAEntityState,
    MappedDevice,
)
from .registry import (
    MLMappingRegistry,
    AIR_CONDITIONER_TYPE,
    COLOR_TEMP_LIGHT_TYPE,
    DIMMABLE_LIGHT_TYPE,
    ELECTRIC_LOCK_TYPE,
    OBSERVED_BASIC_LIGHT_TYPE,
    PANEL_AIR_CONDITIONER_TYPE,
    PANEL_BUTTON_TYPE,
    PANEL_CURTAIN_TYPE,
    PANEL_EVENT_TYPE,
    PANEL_FLOOR_HEAT_TYPE,
    PANEL_VENTILATION_TYPE,
    RELAY_CONTAINER_TYPE,
    RGB_LIGHT_TYPE,
    SENSOR_TYPES,
    UNKNOWN_DO_TYPE,
    FLOOR_HEAT_TYPE,
    VENTILATION_TYPES,
    CURTAIN_TYPES,
    create_default_registry,
)
from .relay import RelayLightMapper

__all__ = (
    "ContainerMapper",
    "CurtainMapper",
    "CURTAIN_TYPES",
    "AirConditionerMapper",
    "AIR_CONDITIONER_TYPE",
    "COLOR_TEMP_LIGHT_TYPE",
    "DIMMABLE_LIGHT_TYPE",
    "ELECTRIC_LOCK_TYPE",
    "FallbackMapper",
    "FloorHeatMapper",
    "FLOOR_HEAT_TYPE",
    "HACommand",
    "HADeviceSpec",
    "HAEntitySpec",
    "HAEntityState",
    "LightMapper",
    "LockMapper",
    "MLDeviceMapper",
    "MLMappingError",
    "MLMappingRegistry",
    "MappedDevice",
    "OBSERVED_BASIC_LIGHT_TYPE",
    "PANEL_AIR_CONDITIONER_TYPE",
    "PANEL_BUTTON_TYPE",
    "PANEL_CURTAIN_TYPE",
    "PANEL_EVENT_TYPE",
    "PANEL_FLOOR_HEAT_TYPE",
    "PANEL_VENTILATION_TYPE",
    "PanelEventMapper",
    "PanelFloorHeatMapper",
    "PlaceholderMapper",
    "RELAY_CONTAINER_TYPE",
    "RGB_LIGHT_TYPE",
    "SENSOR_TYPES",
    "RelayLightMapper",
    "SensorMapper",
    "UNKNOWN_DO_TYPE",
    "UnsupportedHACommandError",
    "VentilationMapper",
    "VENTILATION_TYPES",
    "create_default_registry",
)
