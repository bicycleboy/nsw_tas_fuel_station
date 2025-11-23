"""Custom types for nsw_fuel_ui."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from nsw_fuel import Station

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.loader import Integration

    from nsw_fuel import NSWFuelApiClient
    from .coordinator import NSWFuelCoordinator


type NSWFuelConfigEntry = ConfigEntry[NSWFuelData]


@dataclass
class NSWFuelData:
    """Data for the NSWFuel Iintegration."""

    client: NSWFuelApiClient
    coordinator: NSWFuelCoordinator
    integration: Integration

@dataclass
class StationPriceData:
    """Data structure for O(1) price and name lookups."""
    stations: dict[int, Station]        # station metadata keyed by station_code
    prices: dict[tuple[int, str], float]  # prices keyed by (station_code, fuel_type)

