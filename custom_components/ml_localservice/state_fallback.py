"""Display-only handling of missing ML telemetry; never used for controls."""

from __future__ import annotations

from dataclasses import replace

from .mapping.models import HAEntitySpec, HAEntityState

ATTR_STATE_SOURCE = "state_source"
_POSITION_POINTS = frozenset({"position", "day_position", "night_position", "2"})


class MissingStateFallback:
    """Keep reported state for one entity during its current loaded lifetime."""

    def __init__(self) -> None:
        self._context: object = None
        self._last_reported: HAEntityState | None = None

    def reset(self) -> None:
        """Discard history after removal or a change of mapping/schema."""
        self._context = None
        self._last_reported = None

    def apply(
        self,
        spec: HAEntitySpec,
        state: HAEntityState,
        *,
        enabled: bool,
        synchronized: bool,
        missing: bool,
        context: object,
    ) -> HAEntityState:
        """Use history/defaults only for absent telemetry on a healthy gateway."""
        if context != self._context:
            self.reset()
            self._context = context
        if not enabled or not synchronized:
            return state
        if state.available:
            # Measurements always become unknown when missing, never a stale
            # measurement or a fabricated zero in HA statistics.
            if spec.platform != "sensor" and "invalid_ml_value" not in state.attributes:
                self._last_reported = state
            return self._with_source(state, "reported")
        if not missing or "invalid_ml_value" in state.attributes:
            return state
        if spec.platform != "sensor" and self._last_reported is not None:
            previous = self._last_reported
            if spec.platform == "cover":
                # A historical movement indication cannot establish that a
                # motor is still moving when its current report is absent.
                previous = replace(previous, attributes={
                    key: value for key, value in previous.attributes.items()
                    if key not in {"is_opening", "is_closing"}
                })
            elif spec.platform == "climate":
                previous = replace(previous, attributes={
                    key: value for key, value in previous.attributes.items()
                    if key not in {"current_temperature", "current_humidity"}
                })
            return self._with_source(previous, "last_known")
        if spec.platform in {"light", "switch"}:
            return self._with_source(
                HAEntityState(spec.mapping_key, False, True), "default"
            )
        if spec.platform == "cover":
            attributes: dict[str, object] = {"is_closed": True}
            if _POSITION_POINTS.intersection(spec.consumed_fun_nos):
                # This value is already in HA coordinates, even for an
                # inverted curtain; no ML position or control is synthesized.
                attributes["current_cover_position"] = 0
            return self._with_source(
                HAEntityState(spec.mapping_key, None, True, attributes), "default"
            )
        return self._with_source(
            HAEntityState(spec.mapping_key, None, True, state.attributes), "unknown"
        )

    @staticmethod
    def _with_source(state: HAEntityState, source: str) -> HAEntityState:
        return replace(state, attributes={**state.attributes, ATTR_STATE_SOURCE: source})
