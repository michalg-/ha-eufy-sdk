"""DataUpdateCoordinator for eufy_sdk — owns the bridge connection + the device list."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from homeassistant.core import callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import EufySdkApiClientAuthenticationError, EufySdkApiClientError

if TYPE_CHECKING:
    from .data import EufySdkConfigEntry


class EufySdkDataUpdateCoordinator(DataUpdateCoordinator[dict[str, dict]]):
    """Keep the bridge connected and expose the device list as `{sn: device}`."""

    config_entry: EufySdkConfigEntry

    # Anker Solix devices (separate account/backend), kept apart from the eufy `data`
    # so the eufy platforms never iterate them: `{sn: {productCode, name, category,
    # capabilities, values, ...}}`. Empty unless the bridge has SOLIX_* configured;
    # reassigned per-update, so the class-level {} is only an initial fallback. Live
    # values arrive via `solixReading` events.
    solix_devices: ClassVar[dict[str, dict]] = {}
    _bridge_refresh_generation = 0
    _completed_bridge_refresh_generation = 0

    async def async_force_bridge_refresh(self) -> None:
        """Refresh HA state from a newly fetched SDK/cloud device snapshot."""
        self._bridge_refresh_generation += 1
        await self.async_request_refresh()

    @callback
    def apply_property_changed(self, event: dict[str, Any]) -> bool:
        """Land one bridge property event directly in coordinator state."""
        sn = event.get("deviceSn")
        prop = event.get("property")
        if not isinstance(sn, str) or not isinstance(prop, str) or "value" not in event:
            return False
        current = self.data.get(sn)
        if current is None:
            return False
        devices = dict(self.data)
        device = dict(current)
        state = dict(device.get("state") or {})
        state[prop] = event["value"]
        device["state"] = state
        devices[sn] = device
        self.async_set_updated_data(devices)
        return True

    async def _async_update_data(self) -> dict[str, dict]:
        """Ensure the connection is up, confirm we're authed, and return the devices."""
        client = self.config_entry.runtime_data.client
        try:
            if not client.connected:
                await client.connect()
            auth = await client.auth_status()
            state = auth.get("state")
            if state in ("require_2fa", "require_captcha"):
                # Genuinely needs the user — start the reauth flow.
                msg = f"bridge needs re-authentication (state: {state})"
                raise ConfigEntryAuthFailed(msg)
            if state != "ok":
                # Transient: the bridge is still booting/logging in ("pending" after a
                # restart). Retry next interval instead of freezing the entry in reauth;
                # one boot-window poll must not stop updates indefinitely.
                msg = f"bridge not ready yet (state: {state})"
                raise UpdateFailed(msg)
            refresh_generation = self._bridge_refresh_generation
            force_bridge_refresh = (
                refresh_generation != self._completed_bridge_refresh_generation
            )
            devices = await client.list_devices(refresh=force_bridge_refresh)
            if force_bridge_refresh:
                # Record only the generation this request fulfilled. If another write
                # arrived while it was in flight, the newer generation remains pending.
                self._completed_bridge_refresh_generation = refresh_generation
        except EufySdkApiClientAuthenticationError as err:
            raise ConfigEntryAuthFailed(err) from err
        except EufySdkApiClientError as err:
            raise UpdateFailed(err) from err
        # Solix is optional + independent: a hiccup must not fail the eufy update.
        try:
            solix = await client.list_solix_devices()
            self.solix_devices = {d["sn"]: d for d in solix if d.get("sn")}
        except EufySdkApiClientError:
            self.solix_devices = getattr(self, "solix_devices", {})
        return {d["sn"]: d for d in devices if d.get("sn")}
