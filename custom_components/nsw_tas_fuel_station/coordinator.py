"""DataUpdateCoordinator for NSW Fuel Check."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from nsw_tas_fuel import (
    NSWFuelApiClient,
    NSWFuelApiClientAuthError,
    NSWFuelApiClientError,
    Price,
    StationPrice,
)

from .const import (
    CHEAPEST_RESULTS_LIMIT,
    CONF_AU_STATE,
    CONF_CHEAPEST_FUEL_TYPE,
    CONF_EXCLUDE_STRING,
    CONF_LATITUDE,
    CONF_LOCATION,
    CONF_LONGITUDE,
    CONF_RADIUS_KM,
    CONF_STATION_ADDRESS,
    CONF_STATION_CODE,
    CONF_STATION_NAME,
    DEFAULT_FUEL_TYPE,
    DEFAULT_FUEL_TYPE_NON_E10,
    DEFAULT_RADIUS_KM,
    DOMAIN,
    E10_AVAILABLE_STATES,
)
from .data import CoordinatorData, StationKey

if TYPE_CHECKING:
    from datetime import timedelta

    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


class NSWFuelCoordinator(DataUpdateCoordinator[CoordinatorData]):
    """Manages updates from NSW Fuel Check API."""

    data: CoordinatorData

    def __init__(
        self,
        hass: HomeAssistant,
        api: NSWFuelApiClient,
        nicknames: dict[str, dict[str, Any]],
        scan_interval: timedelta,
    ) -> None:
        """Initialize data updater."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=scan_interval,
        )

        self.api = api

        # Build a deduplicated set of station keys used for fetching prices
        self._station_keys: set[StationKey] = set()
        for nickname_data in nicknames.values():
            for station in nickname_data.get("stations", []):
                self._station_keys.add(
                    (station[CONF_STATION_CODE], station[CONF_AU_STATE])
                )

        # Build a lookup for nickname, lat, lon, state for cheapest fuel queries
        self._cheapest_lookup: dict[str, dict[str, Any]] = {}
        for nickname, nickname_data in nicknames.items():
            location = nickname_data.get(CONF_LOCATION, {})
            lat = location.get(CONF_LATITUDE)
            lon = location.get(CONF_LONGITUDE)
            radius_km = nickname_data.get(CONF_RADIUS_KM, DEFAULT_RADIUS_KM)

            stations = nickname_data.get("stations", [])
            # Use state of first station, as a nickname spanning states illogical/unsupported
            au_state = stations[0][CONF_AU_STATE] if stations else None
            exclude_string = nickname_data.get(CONF_EXCLUDE_STRING, "")
            cheapest_fuel_type = nickname_data.get(
                CONF_CHEAPEST_FUEL_TYPE
            ) or state_default_fuel(au_state)
            self._cheapest_lookup[nickname] = {
                CONF_LATITUDE: lat,
                CONF_LONGITUDE: lon,
                CONF_AU_STATE: au_state,
                CONF_RADIUS_KM: radius_km,
                CONF_CHEAPEST_FUEL_TYPE: cheapest_fuel_type,
                CONF_EXCLUDE_STRING: exclude_string,
            }

    async def _async_update_data(self) -> CoordinatorData:
        """Fetch updated fuel prices for all configured stations."""
        try:
            favorites = await self._update_favorite_stations()

            cheapest = await self._update_cheapest_stations()

        except NSWFuelApiClientAuthError:
            _LOGGER.error("Authentication failed")
            raise ConfigEntryAuthFailed from None

        except NSWFuelApiClientError as err:
            msg = f"Error fetching NSW Fuel API: {err}"
            _LOGGER.error("%s", msg)
            raise UpdateFailed(msg) from err

        except Exception as err:
            msg = f"Unexpected error fetching data: {err}"
            _LOGGER.error("%s", msg)
            raise UpdateFailed(msg) from err

        return {
            "favorites": favorites,
            "cheapest": cheapest,
        }

    async def _update_favorite_stations(self) -> dict[StationKey, dict[str, Price]]:
        """Fetch prices for user's favorite stations.

        Returns:
            Dict mapping station keys (station_code, au_state) to dictionaries
            of fuel types and their corresponding prices.
            {
                (station_code, au_state): {
                    "fuel_type": Price,
                    ...
                },
                ...
            }

        """
        favorites: dict[StationKey, dict[str, Price]] = {}

        for station_code, au_state in self._station_keys:
            prices: list[Price] = await self.api.get_fuel_prices_for_station(
                str(station_code),
                au_state,
            )

            favorites[(station_code, au_state)] = {
                p.fuel_type: p for p in prices if p.fuel_type and p.price is not None
            }

        return favorites

    async def _update_cheapest_stations(self) -> dict[str, list[dict]]:
        """Fetch cheapest fuel prices per nickname.

        Returns:
            {
                nickname: [
                    {
                        "price": float,
                        "station_code": int,
                        "station_name": str,
                        CONF_AU_STATE: str,
                        "fuel_type": str,
                        "last_updated": str,
                    },
                    ...
                ]
            }
        """

        cheapest: dict[str, list[dict]] = {}

        for nickname, nickname_attr in self._cheapest_lookup.items():
            lat = nickname_attr[CONF_LATITUDE]
            lon = nickname_attr[CONF_LONGITUDE]
            radius_km = nickname_attr[CONF_RADIUS_KM]
            fuel_type = nickname_attr[CONF_CHEAPEST_FUEL_TYPE]
            exclude_string = nickname_attr[CONF_EXCLUDE_STRING]

            if lat is None or lon is None:
                _LOGGER.warning("Nickname '%s' missing lat/lon, skipping", nickname)
                continue

            nearby = await self.api.get_fuel_prices_within_radius(
                latitude=lat,
                longitude=lon,
                radius=radius_km,
                fuel_type=fuel_type,
            )

            if not nearby:
                _LOGGER.warning("No prices returned for %s", nickname)
                continue

            # Track cheapest StationPrice per station
            cheapest_per_station: dict[int, StationPrice] = {}

            for sp in nearby:
                code = sp.station.code
                existing = cheapest_per_station.get(code)

                if existing is None or sp.price.price < existing.price.price:
                    cheapest_per_station[code] = sp

            # Convert only the winners, excluding any stations matching the exclude string
            combined: list[dict] = [
                {
                    "price": sp.price.price,
                    CONF_STATION_CODE: sp.station.code,
                    CONF_STATION_NAME: sp.station.name,
                    CONF_STATION_ADDRESS: sp.station.address,
                    CONF_AU_STATE: sp.station.au_state,
                    "fuel_type": sp.price.fuel_type,
                    "last_updated": sp.price.last_updated,
                }
                for sp in cheapest_per_station.values()
                if not exclude_string
                or exclude_string.lower() not in sp.station.name.lower()
            ]

            combined.sort(key=lambda x: x["price"])

            if len(combined) == 1:
                _LOGGER.debug(
                    "For nickname %s, NSW Fuel API returned only one station for lat=%s lon=%s. Try changing the location",
                    nickname,
                    lat,
                    lon,
                )

            cheapest[nickname] = combined[:CHEAPEST_RESULTS_LIMIT]

        return cheapest

    @property
    def nicknames(self) -> list[str]:
        """Return list of configured nicknames."""
        return list(self._cheapest_lookup.keys())

    def get_nickname_cheapest_fuel_type(self, nickname: str) -> str:
        """Return the string used to search for cheapest fuel type for a nickname."""
        return self._cheapest_lookup[nickname][CONF_CHEAPEST_FUEL_TYPE]


def state_default_fuel(
    au_state: str | None,
) -> str:
    """Extract default fuel type based on Australian state.

    Probably can be removed if we assume there are no users
    of earlier versions with no cheapest fuel type in their config entry data."""

    if not au_state or au_state not in E10_AVAILABLE_STATES:
        return DEFAULT_FUEL_TYPE_NON_E10

    return DEFAULT_FUEL_TYPE
