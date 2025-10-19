"""Custom types for nsw_fuel_ui."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.loader import Integration

    from .api import NSWFuelApiClient
    from .coordinator import NSWFuelCoordinator


type NSWFuelConfigEntry = ConfigEntry[NSWFuelData]


@dataclass
class NSWFuelData:
    """Data for the NSWFuel Iintegration."""

    client: NSWFuelApiClient
    coordinator: NSWFuelCoordinator
    integration: Integration
