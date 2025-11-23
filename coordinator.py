"""DataUpdateCoordinator for nsw_fuel_ui."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Optional

from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from nsw_fuel import (
    NSWFuelApiClient,
    NSWFuelApiClientAuthError,
    NSWFuelApiClientError,
    Price,
    Station,
)

from .data import StationPriceData

if TYPE_CHECKING:
    from datetime import timedelta
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

class NSWFuelCoordinator(DataUpdateCoordinator[StationPriceData]):
    """Manage data updates from the NSW Fuel API for multiple stations."""

    def __init__(
        self,
        hass: HomeAssistant,
        api: NSWFuelApiClient,
        stations: list[int],
        station_info: dict[int, Any],
        scan_interval: timedelta,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name="nsw_fuel_multi_station",
            update_interval=scan_interval,
        )
        self.api = api
        self.stations = stations  # List of station codes to fetch
        self.station_info = station_info  # Raw station info dict from config

        # Typed containers for quick access after data updates:
        self._stations_obj: dict[int, Station] = {}  # Latest deserialized stations
        self.prices: dict[tuple[int, str], float] = {}  # {(station_code, fuel_type): price}

    async def _async_update_data(self) -> StationPriceData:
        """
        Fetch latest prices and station data from the API.

        Returns:
            StationPriceData containing:
            - stations: dict mapping station_code -> Station object
            - prices: dict mapping (station_code, fuel_type) -> price
        """
        _LOGGER.debug("_async_update_data called")

        try:
            all_prices: dict[tuple[int, str], float] = {}
            stations_obj: dict[int, Station] = {}

            # Deserialize raw station info dicts into Station objects
            for code, info in self.station_info.items():
                stations_obj[int(code)] = Station.deserialize(info)

            # Fetch fuel prices for each station from the API
            for station_code in self.stations:
                price_list: list[Price] = await self.api.get_fuel_prices_for_station(
                    str(station_code)
                )
                _LOGGER.debug(
                    "Fetched price data for station %s: %s", station_code, price_list
                )

                # Flatten prices into the dict keyed by (station_code, fuel_type)
                for price in price_list:
                    if price.price is not None and price.fuel_type:
                        all_prices[(int(station_code), price.fuel_type)] = price.price

        except NSWFuelApiClientAuthError as err:
            raise ConfigEntryAuthFailed("Authentication failed") from err

        except NSWFuelApiClientError as err:
            raise UpdateFailed(f"Error fetching NSW Fuel data: {err}") from err

        return StationPriceData(
            stations=stations_obj,
            prices=all_prices,
        )


    # --------------------------------------------------------------------
    # Helper methods for sensor and UI layers to access data easily
    # --------------------------------------------------------------------

    def get_station(self, station_code: int) -> Optional[Station]:
        """Return Station object for a given station_code, or None if not found."""
        return self._stations_obj.get(station_code)

    def get_station_name(self, station_code: int) -> str:
        """Return the station name or a fallback string if not found."""
        station = self.get_station(station_code)
        return station.name if station else f"station {station_code}"

    def get_station_state(self, station_code: int) -> str:
        """
        Return the state (e.g. NSW, ACT) for the station.

        Assumes `state` attribute on Station or defaults to 'NSW'.
        """
        station = self.get_station(station_code)
        return getattr(station, "state", "NSW") if station else "NSW"

    def get_station_brand(self, station_code: int) -> Optional[str]:
        """Return the brand name of the station or None if unavailable."""
        station = self.get_station(station_code)
        return station.brand if station else None

    def get_station_fuel_types(self, station_code: int) -> list[str]:
        """
        Return a sorted list of fuel types available for the given station.
        Extracted from the current prices dictionary keys.
        """
        return sorted(
            fuel_type
            for (code, fuel_type) in self.prices.keys()
            if code == station_code
        )

    def get_price(self, station_code: int, fuel_type: str) -> Optional[float]:
        """Return the latest price for the given station code and fuel type."""
        return self.prices.get((station_code, fuel_type))
