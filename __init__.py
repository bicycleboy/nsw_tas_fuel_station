"""
Custom integration to integrate nsw_fuel_ui with Home Assistant.

For more details about this integration, please refer to
git@github.com:bicycleboy/nsw_fuel_ui.git
"""

from __future__ import annotations

import datetime
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.const import Platform, CONF_CLIENT_ID, CONF_CLIENT_SECRET
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from nsw_fuel import NSWFuelApiClient

from .const import DOMAIN
from .coordinator import NSWFuelCoordinator

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.SENSOR]

DEFAULT_SCAN_INTERVAL = datetime.timedelta(minutes=60)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up this integration using UI."""

    session = async_get_clientsession(hass)
    api = NSWFuelApiClient(
        session=session,
        client_id=entry.data[CONF_CLIENT_ID],
        client_secret=entry.data[CONF_CLIENT_SECRET],
    )
    _LOGGER.debug("NSWFuelApiClient created")

    selected_stations: list[int] = entry.data.get("selected_station_codes", [])
    station_info: dict[int, dict[str, Any]] = entry.data.get("station_info", {})

    coordinator = NSWFuelCoordinator(
        hass=hass,
        api=api,
        stations=selected_stations,
        station_info=station_info,
        scan_interval=DEFAULT_SCAN_INTERVAL,
    )

    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Handle removal of an entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
