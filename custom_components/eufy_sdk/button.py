"""Button platform — device-level actions the bridge exposes (reboot, PTZ steps)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.const import EntityCategory

from .entity import EufySdkDeviceEntity

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .coordinator import EufySdkDataUpdateCoordinator
    from .data import EufySdkConfigEntry


# The four movement verbs, ordered as a d-pad reads them. Each is a no-arg method on the
# SDK's `ptz` surface, so `device.action` carries them with no argument mapping at all.
PTZ_STEPS: tuple[tuple[str, str, str], ...] = (
    ("up", "PTZ · Tilt up", "mdi:arrow-up-bold"),
    ("down", "PTZ · Tilt down", "mdi:arrow-down-bold"),
    ("left", "PTZ · Pan left", "mdi:arrow-left-bold"),
    ("right", "PTZ · Pan right", "mdi:arrow-right-bold"),
)


async def async_setup_entry(
    hass: HomeAssistant,  # noqa: ARG001
    entry: EufySdkConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create Reboot (HomeBases), Refresh-Last-Event and PTZ step buttons (cameras)."""
    coordinator = entry.runtime_data.coordinator
    entities: list[ButtonEntity] = [
        EufySdkRebootButton(coordinator, sn)
        for sn, dev in coordinator.data.items()
        if dev.get("canReboot")
    ]
    # A "Refresh Last Event" button per camera/doorbell (same set as the event Image
    # entity, gated on `stream`): forces the bridge to pull the newest event cover now —
    # a manual override for when the auto-refresh raced the HomeBase writing the crop.
    entities.extend(
        EufyRefreshEventButton(coordinator, sn)
        for sn, dev in coordinator.data.items()
        if dev.get("stream")
    )
    # A d-pad per pan-tilt camera, gated on the `ptz` capability the bridge reports — a
    # fixed camera must not get movement buttons it would silently swallow.
    entities.extend(
        EufyPtzButton(coordinator, sn, action, name, icon)
        for sn, dev in coordinator.data.items()
        if "ptz" in dev.get("capabilities", [])
        for action, name, icon in PTZ_STEPS
    )
    # Calibration rides the same capability: a sweep to the stops and back to
    # centre, for a camera whose presets have drifted. A service action rather than
    # a control, so it sits under Configuration instead of among the d-pad.
    entities.extend(
        EufyPtzCalibrateButton(coordinator, sn)
        for sn, dev in coordinator.data.items()
        if "ptz" in dev.get("capabilities", [])
    )
    async_add_entities(entities)


class EufySdkRebootButton(EufySdkDeviceEntity, ButtonEntity):
    """Reboot a HomeBase — a device-level action, not a writable property."""

    _attr_device_class = ButtonDeviceClass.RESTART
    _attr_entity_category = EntityCategory.CONFIG
    _attr_name = "System · Reboot"

    def __init__(self, coordinator: EufySdkDataUpdateCoordinator, sn: str) -> None:
        """Bind to a HomeBase serial."""
        super().__init__(coordinator, sn)
        self._attr_unique_id = f"{sn}_reboot"

    async def async_press(self) -> None:
        """Reboot the HomeBase (it drops offline for a minute or two)."""
        client = self.coordinator.config_entry.runtime_data.client
        await client.reboot(self._sn)


class EufyRefreshEventButton(EufySdkDeviceEntity, ButtonEntity):
    """Force a 'Last event' image refresh — pull the newest event cover now."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:image-refresh"
    _attr_name = "Video · Refresh last event"

    def __init__(self, coordinator: EufySdkDataUpdateCoordinator, sn: str) -> None:
        """Bind to a camera/doorbell serial."""
        super().__init__(coordinator, sn)
        self._attr_unique_id = f"{sn}_refresh_last_event"

    async def async_press(self) -> None:
        """Ask the bridge to re-pull the newest event cover (nudges the Image)."""
        client = self.coordinator.config_entry.runtime_data.client
        await client.refresh_event_image(self._sn)


class EufyPtzButton(EufySdkDeviceEntity, ButtonEntity):
    """
    One pan-tilt step, in the direction this button carries.

    Fire-and-forget: P2P sends no acknowledgement, so a press that returns without
    raising means the frame left for the camera, not that it finished moving. Where the
    camera ended up arrives separately as a `ptzNotify` event, which the Image entity
    already watches to re-pull a stale picture.
    """

    def __init__(
        self,
        coordinator: EufySdkDataUpdateCoordinator,
        sn: str,
        action: str,
        name: str,
        icon: str,
    ) -> None:
        """Bind to a camera serial and the SDK verb this button sends."""
        super().__init__(coordinator, sn)
        self._action = action
        self._attr_unique_id = f"{sn}_ptz_{action}"
        self._attr_name = name
        self._attr_icon = icon

    async def async_press(self) -> None:
        """Step the camera one notch in this button's direction."""
        client = self.coordinator.config_entry.runtime_data.client
        await client.action(self._sn, self._action)


class EufyPtzCalibrateButton(EufySdkDeviceEntity, ButtonEntity):
    """
    Re-centre the pan/tilt mechanism.

    The camera sweeps to its stops and back: a few seconds, and the view moves.
    Fire-and-forget like every PTZ command — the press returns when the frame
    leaves, not when the sweep ends.
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:crosshairs-gps"
    _attr_name = "PTZ · Calibrate"

    def __init__(self, coordinator: EufySdkDataUpdateCoordinator, sn: str) -> None:
        """Bind to a pan-tilt camera serial."""
        super().__init__(coordinator, sn)
        self._attr_unique_id = f"{sn}_ptz_calibrate"

    async def async_press(self) -> None:
        """Start a calibration sweep."""
        client = self.coordinator.config_entry.runtime_data.client
        await client.action(self._sn, "calibrate")
