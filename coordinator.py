"""DataUpdateCoordinator for nsw_fuel_ui."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from nsw_fuel import (
    GetReferenceDataResponse,
    NSWFuelApiClient,
    NSWFuelApiClientAuthError,
    NSWFuelApiClientError,
)

from .const import REF_DATA_REFRESH_DAYS

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


class NSWFuelCoordinator(DataUpdateCoordinator):
    """Manage data updates from the NSW Fuel API."""

    def __init__(
        self, hass: HomeAssistant, api: NSWFuelApiClient, station_code: str
    ) -> None:
        """Initialize."""
        super().__init__(
            hass,
            _LOGGER,
            name=f"nsw_fuel_{station_code}",
            update_interval=timedelta(hours=24),
        )
        self.api = api
        self.station_code = station_code
        self._last_ref_update = datetime(1, 1, 1, tzinfo=UTC)
        self._reference_data: GetReferenceDataResponse | None = None

    def _needs_ref_update(self, now: datetime) -> bool:
        """Return True if reference data should be refreshed."""
        return (
            self._reference_data is None
            or (now - self._last_ref_update).days >= REF_DATA_REFRESH_DAYS
        )

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            now = datetime.now(UTC)

            # Refresh reference data monthly
            if self._needs_ref_update(now):
                self._reference_data = await self.api.get_reference_data()
                self._last_ref_update = now

            # Fetch station prices from API client
            price_list = await self.api.get_fuel_prices_for_station(self.station_code)

            _LOGGER.debug("_async_update_data Fetched price data: %s", price_list)

            return {
                "reference": self._reference_data,
                "prices": price_list,
            }

        except NSWFuelApiClientAuthError as err:
            raise ConfigEntryAuthFailed("Authentication failed") from err

        except NSWFuelApiClientError as err:
            raise UpdateFailed(f"Error fetching NSW Fuel data: {err}") from err
