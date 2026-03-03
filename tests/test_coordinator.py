"""Tests for NSWFuelCoordinator."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed
from nsw_tas_fuel import (
    NSWFuelApiClientAuthError,
    NSWFuelApiClientError,
)

from custom_components.nsw_tas_fuel_station.coordinator import NSWFuelCoordinator

from .conftest import (
    HOBART_LAT,
    HOBART_LNG,
    HOME_LAT,
    HOME_LNG,
    STATION_NSW_A,
    STATION_NSW_B,
    STATION_NSW_C,
)


@pytest.fixture
def nicknames_home_only() -> dict:
    """Single NSW nickname."""
    return {
        "Home": {
            "location": {"latitude": HOME_LAT, "longitude": HOME_LNG},
            "stations": [
                {
                    "station_code": STATION_NSW_A,
                    "au_state": "NSW",
                    "fuel_types": ["U91", "E10"],
                }
            ],
        }
    }


@pytest.fixture
def nicknames_home_and_hobart() -> dict:
    """NSW + TAS nicknames."""
    return {
        "Home": {
            "location": {"latitude": HOME_LAT, "longitude": HOME_LNG},
            "stations": [],
        },
        "Hobart": {
            "location": {"latitude": HOBART_LAT, "longitude": HOBART_LNG},
            "stations": [],
        },
    }


@pytest.fixture
def coordinator(
    hass: HomeAssistant, mock_api_client, nicknames_home_only
) -> NSWFuelCoordinator:
    """Coordinator with a single NSW nickname."""
    return NSWFuelCoordinator(
        hass=hass,
        api=mock_api_client,
        nicknames=nicknames_home_only,
        scan_interval=timedelta(minutes=5),
    )


async def test_async_update_data_success(coordinator: NSWFuelCoordinator) -> None:
    """Coordinator returns favorites and cheapest data."""
    data = await coordinator._async_update_data()

    assert "favorites" in data
    assert "cheapest" in data

    assert isinstance(data["favorites"], dict)
    assert isinstance(data["cheapest"], dict)
    assert "Home" in data["cheapest"]


async def test_update_favorite_stations(coordinator: NSWFuelCoordinator) -> None:
    """Favorites map station key to fuel prices."""
    favorites = await coordinator._update_favorite_stations()

    assert (STATION_NSW_A, "NSW") in favorites
    fuels = favorites[(STATION_NSW_A, "NSW")]

    assert "U91" in fuels
    assert "E10" in fuels
    assert fuels["U91"].price is not None
    assert fuels["E10"].price is not None


async def test_update_cheapest_stations(hass: HomeAssistant, mock_api_client) -> None:
    """NSW nickname returns stations sorted by cheapest price.

    We rely on the FUEL_PRICES mapping in tests/conftest.py; the
    default state for NSW supports combo codes so the coordinator will use
    the cheaper price per station regardless of fuel type.  Given the prices
    in the fixture the expected order is C (162.2) then A (165.3) then B
    (167.8).
    """
    nicknames = {
        "Home": {
            "location": {"latitude": HOME_LAT, "longitude": HOME_LNG},
            "stations": [
                {
                    "station_code": STATION_NSW_A,
                    "au_state": "NSW",
                    "fuel_types": ["U91", "E10", "DL"],
                },
                {
                    "station_code": STATION_NSW_B,
                    "au_state": "NSW",
                    "fuel_types": ["U91", "E10", "DL"],
                },
                {
                    "station_code": STATION_NSW_C,
                    "au_state": "NSW",
                    "fuel_types": ["U91", "E10", "DL"],
                },
            ],
        }
    }
    coordinator = NSWFuelCoordinator(
        hass=hass,
        api=mock_api_client,
        nicknames=nicknames,
        scan_interval=timedelta(minutes=5),
    )

    cheapest = await coordinator._update_cheapest_stations()

    assert "Home" in cheapest
    home = cheapest["Home"]
    assert len(home) == 3

    # verify the order by station_code
    codes = [entry["station_code"] for entry in home]
    assert codes == [STATION_NSW_C, STATION_NSW_A, STATION_NSW_B]


async def test_async_update_auth_failure(
    hass: HomeAssistant, mock_api_client, nicknames_home_only
) -> None:
    """Auth error raises ConfigEntryAuthFailed."""
    mock_api_client.get_fuel_prices_for_station = AsyncMock(
        side_effect=NSWFuelApiClientAuthError("bad auth")
    )

    coordinator = NSWFuelCoordinator(
        hass=hass,
        api=mock_api_client,
        nicknames=nicknames_home_only,
        scan_interval=timedelta(minutes=5),
    )

    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()


async def test_async_update_api_failure(
    hass: HomeAssistant, mock_api_client, nicknames_home_only
) -> None:
    """API error raises UpdateFailed."""
    mock_api_client.get_fuel_prices_for_station = AsyncMock(
        side_effect=NSWFuelApiClientError("boom")
    )

    coordinator = NSWFuelCoordinator(
        hass=hass,
        api=mock_api_client,
        nicknames=nicknames_home_only,
        scan_interval=timedelta(minutes=5),
    )

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()


def test_nicknames_property(coordinator: NSWFuelCoordinator) -> None:
    """Nicknames property exposes configured nicknames."""
    names = coordinator.nicknames

    assert names == ["Home"]
