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

from custom_components.nsw_tas_fuel_station.const import (
    CONF_CHEAPEST_FUEL_TYPE,
    CONF_EXCLUDE_STRING,
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


@pytest.mark.parametrize(
    ("exclude_string", "expected_codes"),
    [
        pytest.param(
            "",
            [STATION_NSW_C, STATION_NSW_A, STATION_NSW_B],
            id="without-exclusions",
        ),
        pytest.param(
            "Ampol",
            [STATION_NSW_C, STATION_NSW_B],
            id="exclude-ampol",
        ),
    ],
)
async def test_update_cheapest_stations(
    hass: HomeAssistant,
    mock_api_client,
    exclude_string: str,
    expected_codes: list[int],
) -> None:
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
            CONF_EXCLUDE_STRING: exclude_string,
            "stations": [
                {
                    "station_code": STATION_NSW_A,
                    "au_state": "NSW",
                    "station_name": "Ampol Foodary Batemans Bay",
                    "fuel_types": ["U91", "E10", "DL"],
                },
                {
                    "station_code": STATION_NSW_B,
                    "au_state": "NSW",
                    "station_name": "Ultra Petroleum Ultra Mogo",
                    "fuel_types": ["U91", "E10", "DL"],
                },
                {
                    "station_code": STATION_NSW_C,
                    "au_state": "NSW",
                    "station_name": "Shell Merimbula",
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
    assert len(home) == len(expected_codes)

    codes = [entry["station_code"] for entry in home]
    assert codes == expected_codes


@pytest.mark.parametrize(
    ("stored_fuel_type", "expected_fuel_type"),
    [
        ("DL", "DL"),
        (None, "E10-U91"),
    ],
    ids=["stored-value-used", "missing-value-falls-back-to-default"],
)
async def test_update_cheapest_stations_uses_nickname_fuel_type(
    hass: HomeAssistant,
    mock_api_client,
    stored_fuel_type: str | None,
    expected_fuel_type: str,
) -> None:
    """The cheapest query should prefer the saved nickname fuel type."""
    nicknames = {
        "Home": {
            "location": {"latitude": HOME_LAT, "longitude": HOME_LNG},
            CONF_CHEAPEST_FUEL_TYPE: stored_fuel_type,
            CONF_EXCLUDE_STRING: "",
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

    await coordinator._update_cheapest_stations()

    assert (
        mock_api_client.get_fuel_prices_within_radius.await_args.kwargs["fuel_type"]
        == expected_fuel_type
    )


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


async def test_refresh_accounts_for_api_operations(
    coordinator: NSWFuelCoordinator, mock_api_client
) -> None:
    """A refresh accounts for favorite and cheapest API operations."""
    await coordinator._async_update_data()

    assert coordinator.last_refresh_api_operations == {
        "favorite_station": 1,
        "cheapest_nearby": 1,
    }
    assert coordinator.api_operations_total == 2
    assert mock_api_client.get_fuel_prices_for_station.await_count == 1
    assert mock_api_client.get_fuel_prices_within_radius.await_count == 1


async def test_duplicate_favorite_station_is_fetched_once_per_refresh(
    hass: HomeAssistant, mock_api_client
) -> None:
    """A favorite shared by nicknames is fetched once while Cheapest stays per nickname."""
    shared_station = {
        "station_code": STATION_NSW_A,
        "au_state": "NSW",
        "fuel_types": ["U91"],
    }
    nicknames = {
        "Home": {
            "location": {"latitude": HOME_LAT, "longitude": HOME_LNG},
            "stations": [shared_station],
        },
        "Work": {
            "location": {"latitude": HOME_LAT, "longitude": HOME_LNG},
            "stations": [shared_station],
        },
    }
    coordinator = NSWFuelCoordinator(
        hass=hass,
        api=mock_api_client,
        nicknames=nicknames,
        scan_interval=timedelta(minutes=5),
    )

    await coordinator._async_update_data()

    assert coordinator.last_refresh_api_operations == {
        "favorite_station": 1,
        "cheapest_nearby": 2,
    }
    assert coordinator.api_operations_total == 3
    assert mock_api_client.get_fuel_prices_for_station.await_count == 1
    assert mock_api_client.get_fuel_prices_within_radius.await_count == 2


async def test_refresh_logs_http_request_delta_when_client_supports_accounting(
    coordinator: NSWFuelCoordinator, mock_api_client, caplog
) -> None:
    """A refresh logs HTTP request deltas when the client exposes counters."""
    counts = {"oauth": 1, "data": 4, "retries": 0}
    mock_api_client.http_request_counts = counts
    mock_api_client.last_token_expires_in = 43200

    async def _favorite_with_count(station_code: str, au_state: str):
        counts["data"] += 1
        return []

    async def _cheapest_with_count(**kwargs):
        counts["data"] += 2
        counts["retries"] += 1
        return []

    mock_api_client.get_fuel_prices_for_station.side_effect = _favorite_with_count
    mock_api_client.get_fuel_prices_within_radius.side_effect = _cheapest_with_count

    with caplog.at_level("DEBUG"):
        await coordinator._async_update_data()

    assert "http_requests oauth=0 data=3 retries=1 total=3" in caplog.text
    assert "session_http_total=8" in caplog.text
    assert "token_expires_in=43200" in caplog.text


async def test_refresh_works_without_client_http_accounting(
    coordinator: NSWFuelCoordinator, mock_api_client, caplog
) -> None:
    """Released clients without HTTP counters retain logical-operation logging."""
    mock_api_client.http_request_counts = None

    with caplog.at_level("DEBUG"):
        await coordinator._async_update_data()

    assert "total_api_operations=2 session_total=2" in caplog.text
    assert "http_requests oauth=" not in caplog.text
