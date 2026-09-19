"""The NSW Fuel Check component."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, cast

from homeassistant.const import CONF_CLIENT_ID, CONF_CLIENT_SECRET, Platform
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from nsw_tas_fuel import NSWFuelApiClient

from .const import (
    CONF_AU_STATE,
    CONF_STATION_CODE,
    CONF_STATION_FUEL_TYPES,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)
from .coordinator import NSWFuelCoordinator

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .data import NSWFuelConfigEntry

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR]


async def async_migrate_entry(
    hass: HomeAssistant, entry: NSWFuelConfigEntry
) -> bool:
    """Migrate favorite sensor unique IDs to include their nickname."""
    _LOGGER.debug("Migrating config entry from version %s", entry.version)

    if entry.version < 2:
        nicknames: dict[str, dict[str, Any]] = entry.data.get("nicknames", {})

        device_registry = dr.async_get(hass)
        nickname_by_device_id: dict[str, str] = {}

        for device_entry in dr.async_entries_for_config_entry(
            device_registry, entry.entry_id
        ):
            for nickname in nicknames:
                if (DOMAIN, f"location_{nickname}") in device_entry.identifiers:
                    nickname_by_device_id[device_entry.id] = nickname
                    break

        favorite_unique_ids: dict[str, dict[str, str]] = {}

        for nickname, nickname_data in nicknames.items():
            nickname_unique_ids: dict[str, str] = {}

            for station in nickname_data.get("stations", []):
                station_code = station.get(CONF_STATION_CODE)
                au_state = station.get(CONF_AU_STATE)

                if station_code is None or au_state is None:
                    continue

                for fuel_type in station.get(CONF_STATION_FUEL_TYPES, []):
                    old_unique_id = (
                        f"{DOMAIN}_{station_code}_{au_state}_{fuel_type}"
                    )
                    new_unique_id = (
                        f"{DOMAIN}_{nickname}_{station_code}_{au_state}_{fuel_type}"
                    )
                    nickname_unique_ids[old_unique_id] = new_unique_id

            favorite_unique_ids[nickname] = nickname_unique_ids

        entity_registry = er.async_get(hass)

        for entity_entry in er.async_entries_for_config_entry(
            entity_registry, entry.entry_id
        ):
            if entity_entry.device_id is None:
                continue

            nickname = nickname_by_device_id.get(entity_entry.device_id)
            if nickname is None:
                continue

            new_unique_id = favorite_unique_ids.get(nickname, {}).get(
                entity_entry.unique_id
            )
            if new_unique_id is None:
                continue

            _LOGGER.debug(
                "Migrating entity %s unique ID from %s to %s",
                entity_entry.entity_id,
                entity_entry.unique_id,
                new_unique_id,
            )
            entity_registry.async_update_entity(
                entity_entry.entity_id, new_unique_id=new_unique_id
            )

        hass.config_entries.async_update_entry(entry, version=2)
        _LOGGER.debug("Migration to version 2 successful")

    return True


async def async_setup_entry(hass: HomeAssistant, entry: NSWFuelConfigEntry) -> bool:
    """Set up NSW Fuel Check integration from the config entry."""
    client_id = cast("str", entry.data.get(CONF_CLIENT_ID))
    client_secret = cast("str", entry.data.get(CONF_CLIENT_SECRET))
    nicknames: dict[str, dict[str, Any]] = entry.data.get("nicknames", {})

    session = async_get_clientsession(hass)

    api = NSWFuelApiClient(
        session=session,
        client_id=client_id,
        client_secret=client_secret,
    )

    coordinator = NSWFuelCoordinator(
        hass=hass,
        api=api,
        nicknames=nicknames,
        scan_interval=DEFAULT_SCAN_INTERVAL,
    )

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    try:
        await coordinator.async_config_entry_first_refresh()
    except ConfigEntryNotReady as err:
        _LOGGER.warning("Initial data fetch failed: %s", err)
        raise

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    return True


async def async_reload_entry(hass: HomeAssistant, entry: NSWFuelConfigEntry) -> None:
    """Reload after new entity added to existing service/location/nickname."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: NSWFuelConfigEntry) -> bool:
    """Temporarily remove config entry e.g. disable integration etc."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok


async def async_remove_entry(hass: HomeAssistant, entry: NSWFuelConfigEntry) -> None:
    """Permanently remove config entry and clean up orphan entities."""
    entity_registry = er.async_get(hass)

    for entity_entry in list(entity_registry.entities.values()):
        if entity_entry.config_entry_id == entry.entry_id:
            entity_registry.async_remove(entity_entry.entity_id)

    hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
