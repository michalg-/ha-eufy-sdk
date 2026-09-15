"""Switch platform — writable booleans, plus per-bit switches for known bitfields."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory

from .bespoke import BITFIELD_SWITCHES
from .entity import EufySdkPropertyEntity, classify, has_capability, is_setting
from .light import LIGHT_OWNED_PROPS

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .coordinator import EufySdkDataUpdateCoordinator
    from .data import EufySdkConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,  # noqa: ARG001
    entry: EufySdkConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create a switch per writable bool, and per-bit switches for known bitfields."""
    coordinator = entry.runtime_data.coordinator
    entities: list[SwitchEntity] = []
    for sn in coordinator.data:
        is_smart_light = has_capability(coordinator.data[sn], "smart_light")
        for spec in entry.runtime_data.properties.get(sn, []):
            # The light platform owns lightPower/lightBrightness for smart_light — don't
            # also surface them as a bare switch/number (would double the control).
            if is_smart_light and spec["name"] in LIGHT_OWNED_PROPS:
                continue
            kind = classify(spec)
            if kind == "switch":
                entities.append(EufySdkSwitch(coordinator, sn, spec))
            elif kind == "bitfield" and spec["name"] in BITFIELD_SWITCHES:
                bf = BITFIELD_SWITCHES[spec["name"]]
                entities.extend(
                    EufyBitmaskSwitch(
                        coordinator,
                        sn,
                        spec,
                        {"label": label, "bit": bit, "base": bf["base"]},
                    )
                    for label, bit in bf["bits"].items()
                )
    async_add_entities(entities)


class EufySdkSwitch(EufySdkPropertyEntity, SwitchEntity):
    """A writable boolean property as a switch."""

    def __init__(
        self,
        coordinator: EufySdkDataUpdateCoordinator,
        sn: str,
        spec: dict[str, Any],
    ) -> None:
        """Put a setting toggle under Configuration; leave a primary control up top."""
        super().__init__(coordinator, sn, spec)
        if is_setting(self._prop):
            self._attr_entity_category = EntityCategory.CONFIG

    @property
    def is_on(self) -> bool | None:
        """On when the property's live value is truthy."""
        v = self.prop_value
        return None if v is None else bool(v)

    async def async_turn_on(self, **_: Any) -> None:
        """Set the property true."""
        await self.write(value=True)

    async def async_turn_off(self, **_: Any) -> None:
        """Set the property false."""
        await self.write(value=False)


class EufyBitmaskSwitch(EufySdkPropertyEntity, SwitchEntity):
    """One bit of a bitfield property as a switch (writes back the whole mask)."""

    def __init__(
        self,
        coordinator: EufySdkDataUpdateCoordinator,
        sn: str,
        spec: dict[str, Any],
        bitdef: dict[str, Any],
    ) -> None:
        """Bind to a single bit of the parent bitfield property ({label, bit, base})."""
        super().__init__(coordinator, sn, spec)
        self._bit: int = bitdef["bit"]
        self._base: int = bitdef["base"]
        self._attr_unique_id = f"{sn}_{self._prop}_{self._bit}"
        self._attr_name = bitdef["label"]
        self._attr_entity_category = EntityCategory.CONFIG

    def _mask(self) -> int:
        """Return the current full bitmask value (falls back to the enable base)."""
        v = self.prop_value
        return int(v) if isinstance(v, (int, float)) else self._base

    def _value_from_restored_state(self, _state: str) -> Any:
        """
        Never restore: this holds the FULL mask, not this bit's own on/off.

        The base default would parse a restored "on"/"off" into a bare bool — and
        since `bool` is an `int` subclass, `_mask()`'s `isinstance(v, (int, float))`
        check would accept it and read the mask back as 0 or 1, corrupting every
        OTHER bit on the next write. One bit's last-known state says nothing about
        the other bits it shares a mask with, so there is nothing safe to restore
        here at all.
        """
        return None

    @property
    def is_on(self) -> bool | None:
        """On when this bit is set in the current mask."""
        v = self.prop_value
        return None if v is None else bool(int(v) & self._bit)

    async def async_turn_on(self, **_: Any) -> None:
        """Set this bit (keeping the enable base and the other bits)."""
        await self.write(self._mask() | self._bit | self._base)

    async def async_turn_off(self, **_: Any) -> None:
        """Clear this bit (keeping the enable base and the other bits)."""
        await self.write((self._mask() & ~self._bit) | self._base)
