"""
The eufy_sdk integration — talks to a ha-eufy-sdk bridge over WebSocket.

https://github.com/mega-yfue/ha-eufy-sdk
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from homeassistant.const import Platform
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.loader import async_get_loaded_integration

from .api import EufySdkApiClient
from .bespoke import BITFIELD_SWITCHES
from .const import (
    CONF_HOST,
    CONF_POLL_INTERVAL,
    CONF_PORT,
    DEFAULT_POLL_INTERVAL_MIN,
    DOMAIN,
    LOGGER,
)
from .coordinator import EufySdkDataUpdateCoordinator
from .data import EufySdkData

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .data import EufySdkConfigEntry

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.SWITCH,
    Platform.SELECT,
    Platform.NUMBER,
    Platform.CAMERA,
    Platform.BUTTON,
    Platform.IMAGE,
    Platform.EVENT,
    Platform.LIGHT,
]


async def async_setup_entry(hass: HomeAssistant, entry: EufySdkConfigEntry) -> bool:
    """Set up eufy_sdk from a config entry."""
    poll_min = entry.options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL_MIN)
    coordinator = EufySdkDataUpdateCoordinator(
        hass=hass,
        logger=LOGGER,
        name=DOMAIN,
        # HA reads the bridge at the same cadence the bridge polls the cloud.
        update_interval=timedelta(minutes=poll_min),
        config_entry=entry,
    )

    # Forward every bridge event onto the HA event bus for automations — and recover
    # fast when the bridge comes back. A bridge restart drops the WS and fails one
    # coordinator poll, marking every entity `unavailable`; without a nudge they stay
    # that way (and detections don't show) until the next poll, up to `poll_min` minutes
    # later. So refresh the coordinator immediately on the bridge's `ready` broadcast
    # (sent on every boot) and on a WS reconnect.
    def _refresh_now() -> None:
        hass.async_create_task(coordinator.async_request_refresh())

    def _on_event(evt: dict) -> None:
        hass.bus.async_fire(f"{DOMAIN}_event", evt)
        if evt.get("event") == "ready":
            _refresh_now()

    client = EufySdkApiClient(
        host=entry.data[CONF_HOST],
        port=entry.data[CONF_PORT],
        session=async_get_clientsession(hass),
        on_event=_on_event,
        on_reconnect=_refresh_now,
    )
    entry.runtime_data = EufySdkData(
        client=client,
        integration=async_get_loaded_integration(hass, entry.domain),
        coordinator=coordinator,
    )

    await coordinator.async_config_entry_first_refresh()

    # Push the chosen poll interval to the bridge (the cloud-poll cadence lives there).
    try:
        await client.set_poll_ms(poll_min * 60_000)
    except Exception as err:  # noqa: BLE001 - a failed config push shouldn't block setup
        LOGGER.warning("could not set bridge poll interval: %s", err)

    # Reload when the options change, so a new poll interval is applied.
    entry.async_on_unload(entry.add_update_listener(_async_reload_on_update))

    # Property manifests are static per device — fetch once so the platforms can
    # build switch/select/number/sensor entities. A device that fails is skipped.
    properties: dict[str, list] = {}
    for sn, dev in coordinator.data.items():
        if dev.get("error"):
            continue
        try:
            properties[sn] = await client.get_properties(sn)
        except Exception as err:  # noqa: BLE001 - one bad device must not abort setup
            LOGGER.warning("could not fetch properties for %s: %s", sn, err)
    entry.runtime_data.properties = properties

    _prune_stale_property_entities(hass, entry.entry_id, properties)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


def _prune_stale_property_entities(
    hass: HomeAssistant, entry_id: str, properties: dict[str, list]
) -> None:
    """
    Remove registry entities for properties the bridge no longer advertises.

    The bridge prunes its manifest to what a device actually reads (see its
    `propertySpecs`), but that only changes what gets CREATED — an entity from before
    the prune, or from a property that has simply gone quiet, stays in the registry
    forever: `available` only checks that the device itself is present, never that its
    own property still is, so it sits `unavailable` with no way back short of a manual
    delete. This is the other half of that prune, run every setup so a device that
    drops a property (or a fresh install that prunes from the start) cleans up on its
    own.

    Only two entity shapes are ever touched, both built entirely from `properties` —
    never a static entity (reboot, the PTZ buttons, camera, stream_url, ...), which
    this cannot even name and so cannot remove by construction:
      - `{sn}_{propName}`            — the generic property entity (entity.py)
      - `{sn}_{propName}_{bitmask}`  — one bit of a known bitfield (switch.py), kept
        exactly as long as `propName` itself (e.g. `aiDetectType`) is still current —
        the bitfield property's presence, not the sub-switch's, is what the bridge
        reports.
    """
    current: dict[str, set[str]] = {
        sn: {p["name"] for p in specs} for sn, specs in properties.items()
    }

    registry = er.async_get(hass)
    for entry_entity in er.async_entries_for_config_entry(registry, entry_id):
        sn, _, suffix = entry_entity.unique_id.partition("_")
        names = current.get(sn)
        if names is None or not suffix or suffix in names:
            continue  # wrong device, no property data yet, or still a live property

        bitfield_prop = next(
            (p for p in BITFIELD_SWITCHES if suffix.startswith(f"{p}_")), None
        )
        if bitfield_prop is None:
            continue  # not property-shaped at all (reboot, ptz_*, camera, ...)
        if bitfield_prop in names:
            continue  # the bitfield property is still live — its sub-switch survives

        LOGGER.debug(
            "removing stale entity %s (property gone from the bridge manifest)",
            entry_entity.entity_id,
        )
        registry.async_remove(entry_entity.entity_id)


async def async_unload_entry(hass: HomeAssistant, entry: EufySdkConfigEntry) -> bool:
    """Unload a config entry and close the bridge connection."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.client.close()
    return unloaded


async def _async_reload_on_update(
    hass: HomeAssistant, entry: EufySdkConfigEntry
) -> None:
    """Reload the entry when its options change (e.g. a new poll interval)."""
    await hass.config_entries.async_reload(entry.entry_id)
