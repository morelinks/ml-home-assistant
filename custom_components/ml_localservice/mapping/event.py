"""Mappings for passive, physical panel action notifications."""

from __future__ import annotations

from collections.abc import Mapping

from ..models import MLDevice
from .base import UnsupportedHACommandError, ml_device_spec
from .models import HACommand, HADeviceSpec, HAEntitySpec, HAEntityState, MLCommands


class PanelEventMapper:
    """Expose one physical panel as one passive HA event entity.

    ML sends a real-time notification when somebody presses a panel.  These
    action points are not control endpoints, even when an observed directory
    advertises a writable flag, so this mapper never translates HA commands.
    """

    _actions = (
        ("click", "click"),
        ("dbclick", "double_click"),
        ("longpress", "long_press"),
    )

    def map_device(self, device: MLDevice) -> HADeviceSpec:
        return ml_device_spec(device, "panel_switch")

    def map_entities(self, device: MLDevice) -> tuple[HAEntitySpec, ...]:
        functions = {function.fun_no for function in device.functions}
        consumed = tuple(
            fun_no for fun_no, _event_type in self._actions if fun_no in functions
        )
        if not consumed:
            return ()
        return (
            HAEntitySpec(
                device.c_dev_id,
                "event",
                "panel_action",
                "Panel action",
                consumed,
            ),
        )

    def map_state(
        self, device: MLDevice, states: Mapping[str, str]
    ) -> tuple[HAEntityState, ...]:
        del states
        if not self.map_entities(device):
            return ()
        # Event entities receive their actual type from raw push notifications;
        # a state snapshot only expresses that the source schema is available.
        return (HAEntityState("panel_action", None, True),)

    def map_command(
        self, device: MLDevice, entity: HAEntitySpec, command: HACommand
    ) -> MLCommands:
        del device, entity, command
        raise UnsupportedHACommandError("panel actions are passive ML events")
