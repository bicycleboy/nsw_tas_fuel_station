"""
Custom integration to integrate nsw_fuel_ui with Home Assistant.

For more details about this integration, please refer to
git@github.com:bicycleboy/nsw_fuel_ui.git
"""

from __future__ import annotations

import datetime
import logging
from typing import TYPE_CHECKING

from homeassistant.const import Platform
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from nsw_fuel import NSWFuelApiClient

from .const import DOMAIN
from .coordinator import NSWFuelCoordinator

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.SENSOR]

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

DEFAULT_SCAN_INTERVAL = datetime.timedelta(minutes=60)

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up this integration using UI."""
    session = async_get_clientsession(hass)
    api = NSWFuelApiClient(
        session=session,
        client_id=entry.data["client_id"],
        client_secret=entry.data["client_secret"],
    )
    _LOGGER.debug("NSWFuelApiClient created")

    station_code = entry.data["station_code"]
    coordinator = NSWFuelCoordinator(hass, api, station_code)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Handle removal of an entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
