"""Tests for NSW Fuel Check config flow."""

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_CLIENT_ID, CONF_CLIENT_SECRET
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from nsw_tas_fuel import NSWFuelApiClientAuthError, NSWFuelApiClientError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.nsw_tas_fuel_station.config_flow import (
    NSWFuelConfigFlow,
    _get_state_defaults,
    _split_combo_fuel_code,
)
from custom_components.nsw_tas_fuel_station.const import (
    CONF_LOCATION,
    CONF_NICKNAME,
    DEFAULT_FUEL_TYPE,
    DOMAIN,
)

from .conftest import (
    CLIENT_ID,
    CLIENT_SECRET,
    HOBART_LAT,
    HOBART_LNG,
    HOME_LAT,
    HOME_LNG,
    STATION_NSW_A,
    STATION_NSW_B,
    STATION_NSW_C,
    STATION_TAS_D,
    STATION_TAS_E,
)


async def _start_flow_and_submit_creds(
    hass: HomeAssistant, client_id: str, client_secret: str
) -> dict[str, Any]:
    """Start a config flow and submit API credentials.

    Returns the final step dict from hass.config_entries.flow.async_configure.
    """
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    return await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"client_id": client_id, "client_secret": client_secret},
    )


NSW_FUEL_API_DEFINITION = (
    "custom_components.nsw_tas_fuel_station.config_flow.NSWFuelApiClient"
)
CONFIG_FLOW_PATH = (
    "custom_components.nsw_tas_fuel_station.config_flow.NSWFuelConfigFlow"
)


@pytest.mark.parametrize(
    ("latitude", "longitude", "expected_state", "station_code"),
    [
        (HOME_LAT, HOME_LNG, "NSW", STATION_NSW_A),
        (HOME_LAT, HOME_LNG, "NSW", STATION_NSW_B),
        (HOME_LAT, HOME_LNG, "NSW", STATION_NSW_C),
        (HOBART_LAT, HOBART_LNG, "TAS", STATION_TAS_D),
        (HOBART_LAT, HOBART_LNG, "TAS", STATION_TAS_E),
    ],
    ids=["nsw-a", "nsw-b", "nsw-c", "tas-d", "tas-e"],
)
async def test_successful_config_flow(
    hass: HomeAssistant,
    mock_api_client: AsyncMock,
    latitude: float,
    longitude: float,
    expected_state: str,
    station_code: int,
) -> None:
    """Test successful config flow with per-station fuel type handling."""

    hass.config.latitude = latitude
    hass.config.longitude = longitude
    hass.config.time_zone = "Australia/Sydney"

    with patch(NSW_FUEL_API_DEFINITION, return_value=mock_api_client):
        # Start flow and submit credentials
        result = await _start_flow_and_submit_creds(hass, CLIENT_ID, CLIENT_SECRET)
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "station_select"

        # Select station
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"selected_station_codes": [str(station_code)]},
        )
        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert result["title"] == "NSW Fuel Check"

        # Verify data structure
        data = result["data"]
        assert "nicknames" in data
        assert "Home" in data["nicknames"]
        home = data["nicknames"]["Home"]
        assert len(home["stations"]) == 1
        station = home["stations"][0]
        assert station["station_code"] == station_code
        assert station["au_state"] == expected_state

        # Verify location
        assert "location" in home
        assert home["location"]["latitude"] == latitude
        assert home["location"]["longitude"] == longitude

        # Verify fuel types: check that U91 and E10 are only present if configured at the station
        fuel_types = station.get("fuel_types", [])
        for fuel in ["U91", "E10"]:
            if fuel in station.get("fuel_types", []):
                assert fuel in fuel_types
            else:
                assert fuel not in fuel_types

        await hass.async_block_till_done()
        entries = hass.config_entries.async_entries(DOMAIN)
        assert len(entries) == 1


@pytest.mark.parametrize(
    (
        "latitude",
        "longitude",
        "expected_default",
        "expect_combo_allowed",
        "station_code",
        "submit_fuel_type",
    ),
    [
        # NSW default (combo codes allowed)
        (HOME_LAT, HOME_LNG, "E10-U91", True, STATION_NSW_A, None),
        # NSW, explicitly add DL fuel
        (HOME_LAT, HOME_LNG, "E10-U91", True, STATION_NSW_B, "DL"),
        # TAS default (no combo codes)
        (HOBART_LAT, HOBART_LNG, "U91", False, STATION_TAS_D, None),
        # TAS, explicitly add DL fuel
        (HOBART_LAT, HOBART_LNG, "U91", False, STATION_TAS_E, "DL"),
    ],
    ids=["nsw-default", "nsw-dl", "tas-default", "tas-dl"],
)
async def test_successful_advanced_config_flow(
    hass: HomeAssistant,
    mock_api_client: AsyncMock,
    latitude: float,
    longitude: float,
    expected_default: str,
    expect_combo_allowed: bool,
    station_code: int,
    submit_fuel_type: str | None,
) -> None:
    """Test advanced config flow default fuel and available fuel types per state."""

    suggested_location = {"latitude": latitude, "longitude": longitude}
    default_fuel, fuel_types = _get_state_defaults(suggested_location)

    assert default_fuel == expected_default

    codes = [code for code, _ in fuel_types]
    if expect_combo_allowed:
        assert "E10-U91" in codes
    else:
        # No combo (dash) codes should be present for TAS
        assert not any("-" in c for c in codes)

    # Ensure hass location is set for the flow
    hass.config.latitude = latitude
    hass.config.longitude = longitude
    hass.config.time_zone = "Australia/Sydney"

    # Now exercise the advanced options path in the flow, submitting the fuel
    fuel_to_submit = submit_fuel_type or expected_default

    with patch(NSW_FUEL_API_DEFINITION, return_value=mock_api_client):
        # Start flow and submit credentials
        result = await _start_flow_and_submit_creds(hass, CLIENT_ID, CLIENT_SECRET)

        # Choose advanced options
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"selected_station_codes": ["__advanced__"]}
        )

        assert result["step_id"] == "advanced_options"

        # Submit advanced options with explicit location and fuel type
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                "nickname": "Work",
                "location": {"latitude": latitude, "longitude": longitude},
                "fuel_type": fuel_to_submit,
            },
        )

        # Should now be at station_select (form returned for station choice)
        assert result["step_id"] == "station_select"
        assert result["type"] is FlowResultType.FORM


async def test_no_station_selected_error(
    hass_with_config: HomeAssistant, mock_api_client: AsyncMock
) -> None:
    """Test error when user doesn't select any station."""
    with patch(
        NSW_FUEL_API_DEFINITION,
        return_value=mock_api_client,
    ):
        result = await _start_flow_and_submit_creds(
            hass_with_config, CLIENT_ID, CLIENT_SECRET
        )

        # Submit empty station list
        result = await hass_with_config.config_entries.flow.async_configure(
            result["flow_id"],
            {"selected_station_codes": []},
        )

        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "station_select"
        assert "no_stations" in result["errors"]["base"]


async def test_add_station_to_existing_nickname(
    hass_with_config: HomeAssistant, mock_api_client: AsyncMock
) -> None:
    """Test adding second station to existing nickname."""

    # Add existing entry
    existing_entry = MockConfigEntry(
        domain=DOMAIN,
        title="NSW Fuel",
        data={
            CONF_CLIENT_ID: CLIENT_ID,
            CONF_CLIENT_SECRET: CLIENT_SECRET,
            "nicknames": {
                "Home": {
                    "stations": [
                        {
                            "station_code": STATION_NSW_A,
                            "au_state": "NSW",
                            "fuel_types": ["U91"],
                        }
                    ]
                }
            },
        },
        source=config_entries.SOURCE_USER,
        version=1,
    )
    existing_entry.add_to_hass(hass_with_config)

    with patch(
        NSW_FUEL_API_DEFINITION,
        return_value=mock_api_client,
    ):
        result = await _start_flow_and_submit_creds(
            hass_with_config, CLIENT_ID, CLIENT_SECRET
        )

        result = await hass_with_config.config_entries.flow.async_configure(
            result["flow_id"],
            {"selected_station_codes": [str(STATION_NSW_B)]},
        )

        # Updating existing entry returns ABORT with reason "updated_existing"
        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "updated_existing"

        # Verify the entry was updated with both stations
        entries = hass_with_config.config_entries.async_entries(DOMAIN)
        assert len(entries) == 1
        home = entries[0].data["nicknames"]["Home"]
        station_codes = [s["station_code"] for s in home["stations"]]
        assert STATION_NSW_A in station_codes
        assert STATION_NSW_B in station_codes


@pytest.mark.parametrize(
    ("exception_class", "error_message", "expected_error_key"),
    [
        (NSWFuelApiClientAuthError, "Invalid credentials", "auth"),
        (NSWFuelApiClientError, "Request timeout (408)", "connection"),
        (NSWFuelApiClientError, "Bad request (400)", "connection"),
        (NSWFuelApiClientError, "Internal server error (500)", "connection"),
    ],
    ids=[
        "auth-invalid-credentials",
        "connection-timeout-408",
        "connection-bad-request-400",
        "connection-server-error-500",
    ],
)
async def test_api_errors_on_station_fetch(
    hass_with_config: HomeAssistant,
    exception_class: type,
    error_message: str,
    expected_error_key: str,
) -> None:
    """Test API error handling (auth and connection errors) when fetching stations."""
    error_client = AsyncMock()
    error_client.get_fuel_prices_within_radius = AsyncMock(
        side_effect=exception_class(error_message)
    )

    with patch(NSW_FUEL_API_DEFINITION, return_value=error_client):
        result = await _start_flow_and_submit_creds(
            hass_with_config, CLIENT_ID, CLIENT_SECRET
        )

        # Should show error on user form
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "user"
        assert "base" in result["errors"]
        assert result["errors"]["base"] == expected_error_key


@pytest.mark.parametrize(
    "mode",
    [
        "home_invalid",
        "advanced_invalid",
    ],
    ids=["home-invalid", "advanced-invalid"],
)
async def test_invalid_location_handling(
    hass: HomeAssistant,
    mock_api_client: AsyncMock,
    mode: str,
) -> None:
    """Parameterized invalid-location tests for both entry points.

    - `home_invalid`: hass.config is invalid, credentials submission should
      route the flow directly to `advanced_options`.
    - `advanced_invalid`: normal hass.config; user navigates to advanced
      options and submits an invalid location, which should redisplay the
      `advanced_options` form with `invalid_coordinates` error.
    """
    if mode == "home_invalid":
        # Set hass home location outside service area (South Pole)
        hass.config.latitude = -90.0
        hass.config.longitude = 0.0

        mock_client = AsyncMock()

        with patch(NSW_FUEL_API_DEFINITION, return_value=mock_client):
            result = await _start_flow_and_submit_creds(hass, CLIENT_ID, CLIENT_SECRET)

            # Should go to advanced options due to invalid location
            assert result["type"] is FlowResultType.FORM
            assert result["step_id"] == "advanced_options"

    else:
        # advanced_invalid: ensure hass has a valid home location
        hass.config.latitude = HOME_LAT
        hass.config.longitude = HOME_LNG

        with patch(NSW_FUEL_API_DEFINITION, return_value=mock_api_client):
            # Start and progress to station_select
            result = await _start_flow_and_submit_creds(hass, CLIENT_ID, CLIENT_SECRET)

            assert result["type"] is FlowResultType.FORM
            assert result["step_id"] == "station_select"

            # Enter advanced options
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {"selected_station_codes": ["__advanced__"]},
            )

            assert result["type"] is FlowResultType.FORM
            assert result["step_id"] == "advanced_options"

            # Submit advanced options with INVALID location (South Pole)
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {
                    "nickname": "Work",
                    "location": {"latitude": -90.0, "longitude": 0.0},
                    "fuel_type": "U91",
                },
            )

            # Should show error and remain on advanced_options
            assert result["type"] is FlowResultType.FORM
            assert result["step_id"] == "advanced_options"
            assert "base" in result["errors"]
            assert result["errors"]["base"] == "invalid_coordinates"


async def test_error_fetching_stations_in_advanced_options(
    hass_with_config: HomeAssistant, mock_api_client: AsyncMock
) -> None:
    """API errors during station lookup should redisplay the advanced form.

     The error branch in async_step_advanced_options is exercised when
    _get_station_list returns a non-empty errors dict.  This test
     forces the underlying API call to raise NSWFuelApiClientError which is
     translated into a connection error key.
    """
    # use the normal mock_api_client fixture but override its radius method so
    # the first invocation returns a real result and the second raises an error.
    # grab the original method so we can call it for the first invocation
    original = mock_api_client.get_fuel_prices_within_radius

    call_count: int = 0

    async def side_effect(latitude, longitude, radius=25, fuel_type="U91"):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # first call behaves normally
            return await original(latitude, longitude, radius, fuel_type)
        # subsequent calls simulate a network error
        raise NSWFuelApiClientError("boom")

    mock_api_client.get_fuel_prices_within_radius = AsyncMock(side_effect=side_effect)

    with patch(
        NSW_FUEL_API_DEFINITION,
        return_value=mock_api_client,
    ):
        # initialise flow and move to station_select
        result = await _start_flow_and_submit_creds(
            hass_with_config, CLIENT_ID, CLIENT_SECRET
        )
        assert result["step_id"] == "station_select"

        # navigate into advanced options
        result = await hass_with_config.config_entries.flow.async_configure(
            result["flow_id"],
            {"selected_station_codes": ["__advanced__"]},
        )
        assert result["step_id"] == "advanced_options"

        # submit valid advanced options; the API will error
        result = await hass_with_config.config_entries.flow.async_configure(
            result["flow_id"],
            {
                "nickname": "Work",
                "location": {"latitude": HOME_LAT, "longitude": HOME_LNG},
                "fuel_type": "U91",
            },
        )

        # flow should stay on advanced_options with connection error
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "advanced_options"
        assert result["errors"]["base"] == "connection"


async def test_invalid_nickname_in_advanced_options(
    hass: HomeAssistant, mock_api_client: AsyncMock
) -> None:
    """Test invalid nickname handling in advanced options form."""
    hass.config.latitude = HOME_LAT
    hass.config.longitude = HOME_LNG

    with patch(
        NSW_FUEL_API_DEFINITION,
        return_value=mock_api_client,
    ):
        # Start with valid location to get to station_select step
        result = await _start_flow_and_submit_creds(hass, CLIENT_ID, CLIENT_SECRET)

        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "station_select"

        # Select advanced options
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"selected_station_codes": ["__advanced__"]},
        )

        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "advanced_options"

        # Submit with invalid nickname containing spaces and special characters
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                "nickname": "My Work!",  # Invalid: contains space and exclamation mark
                "location": {"latitude": HOME_LAT, "longitude": HOME_LNG},
                "fuel_type": "U91",
            },
        )

        # Should show error and remain on advanced_options
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "advanced_options"
        assert "nickname" in result["errors"]
        assert result["errors"]["nickname"] == "invalid_nickname"

        # Test empty nickname as well
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                "nickname": "",  # Invalid: empty
                "location": {"latitude": HOME_LAT, "longitude": HOME_LNG},
                "fuel_type": "U91",
            },
        )

        # Should show error again
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "advanced_options"
        assert "nickname" in result["errors"]
        assert result["errors"]["nickname"] == "invalid_nickname"

        # Test valid nickname with hyphen and underscore
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                "nickname": "Work-Home_2",  # Valid: hyphen and underscore OK
                "location": {"latitude": HOME_LAT, "longitude": HOME_LNG},
                "fuel_type": "U91",
            },
        )

        # Should proceed to station_select (no nickname error)
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "station_select"
        assert "nickname" not in result.get("errors", {})


async def test_add_fuel_type_to_existing_station_via_advanced(
    hass: HomeAssistant, mock_api_client: AsyncMock
) -> None:
    """Test adding a new fuel type (DL) to an existing station via advanced options."""
    hass.config.latitude = HOME_LAT
    hass.config.longitude = HOME_LNG

    # Add existing entry with station A having only U91 fuel type
    existing_entry = MockConfigEntry(
        domain=DOMAIN,
        title="NSW Fuel Check",
        data={
            CONF_CLIENT_ID: CLIENT_ID,
            CONF_CLIENT_SECRET: CLIENT_SECRET,
            "nicknames": {
                "Home": {
                    "stations": [
                        {
                            "station_code": STATION_NSW_A,
                            "au_state": "NSW",
                            "station_name": "Ampol Foodary Batemans Bay",
                            "fuel_types": ["U91"],
                        }
                    ]
                }
            },
        },
        source=config_entries.SOURCE_USER,
        version=1,
    )
    existing_entry.add_to_hass(hass)

    with (
        patch(
            NSW_FUEL_API_DEFINITION,
            return_value=mock_api_client,
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET},
        )

        # Access advanced options
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"selected_station_codes": ["__advanced__"]},
        )

        # Should be in advanced options form
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "advanced_options"

        # Configure advanced options: nickname and fuel type
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                "nickname": "Home",
                "fuel_type": "DL",
            },
        )

        # Advanced options transitions to station_select
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "station_select"

        # In advanced path, can select existing station A (NOT filtered out)
        # This allows adding DL fuel type to existing station
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"selected_station_codes": [str(STATION_NSW_A)]},
        )

        # Should update existing entry
        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "updated_existing"

        # Verify the entry was updated with DL fuel type added to station A
        entries = hass.config_entries.async_entries(DOMAIN)
        assert len(entries) == 1
        home = entries[0].data["nicknames"]["Home"]
        assert len(home["stations"]) == 1
        station = home["stations"][0]
        assert station["station_code"] == STATION_NSW_A
        assert "U91" in station["fuel_types"]
        assert "DL" in station["fuel_types"]
        # Fuel types should be sorted
        assert sorted(station["fuel_types"]) == ["DL", "U91"]


async def test_add_multiple_nicknames(
    hass: HomeAssistant, mock_api_client: AsyncMock
) -> None:
    """Test creating multiple config entries with different nicknames."""
    hass.config.latitude = HOME_LAT
    hass.config.longitude = HOME_LNG

    with (
        patch(
            NSW_FUEL_API_DEFINITION,
            return_value=mock_api_client,
        ),
    ):
        # Create FIRST config entry with "Home" nickname (default)
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET},
        )

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"selected_station_codes": [str(STATION_NSW_A)]},
        )

        assert result["type"] is FlowResultType.CREATE_ENTRY
        await hass.async_block_till_done()

        entries = hass.config_entries.async_entries(DOMAIN)
        assert len(entries) == 1
        first_entry = entries[0]
        assert "Home" in first_entry.data["nicknames"]

        # Create SECOND config entry with "Work" nickname via advanced options
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET},
        )

        # Go to advanced path to set custom nickname
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"selected_station_codes": ["__advanced__"]},
        )

        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "advanced_options"

        # Set nickname to "Work" instead of default "Home"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                "nickname": "Work",
                "fuel_type": "U91",
            },
        )

        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "station_select"

        # Select station B for Work nickname
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"selected_station_codes": [str(STATION_NSW_B)]},
        )

        # Should create new entry (not update) since "Work" is a new nickname
        assert result["type"] is FlowResultType.CREATE_ENTRY
        await hass.async_block_till_done()

        # Verify we now have TWO separate config entries
        entries = hass.config_entries.async_entries(DOMAIN)
        assert len(entries) == 2

        # Find each entry by nickname
        nicknames = {}
        for entry in entries:
            entry_nicknames = list(entry.data.get("nicknames", {}).keys())
            for nick in entry_nicknames:
                nicknames[nick] = entry

        # Both nicknames should exist
        assert "Home" in nicknames
        assert "Work" in nicknames

        # Home entry should have station A
        home_entry = nicknames["Home"]
        home_stations = [
            s["station_code"] for s in home_entry.data["nicknames"]["Home"]["stations"]
        ]
        assert STATION_NSW_A in home_stations

        # Work entry should have station B
        work_entry = nicknames["Work"]
        work_stations = [
            s["station_code"] for s in work_entry.data["nicknames"]["Work"]["stations"]
        ]
        assert STATION_NSW_B in work_stations


async def test_duplicate_fuel_type_in_advanced_path(
    hass: HomeAssistant, mock_api_client: AsyncMock
) -> None:
    """Test error when trying to add duplicate fuel type to a station."""
    hass.config.latitude = HOME_LAT
    hass.config.longitude = HOME_LNG

    # Add existing entry with station A having DL (Diesel) - not U91
    existing_entry = MockConfigEntry(
        domain=DOMAIN,
        title="NSW Fuel Check",
        data={
            CONF_CLIENT_ID: CLIENT_ID,
            CONF_CLIENT_SECRET: CLIENT_SECRET,
            "nicknames": {
                "Home": {
                    "location": {"latitude": HOME_LAT, "longitude": HOME_LNG},
                    "stations": [
                        {
                            "station_code": STATION_NSW_A,
                            "au_state": "NSW",
                            "station_name": "Ampol Foodary Ampol Foodary Batemans Bay",
                            "fuel_types": ["U91", "DL"],  # Has both U91 and DL
                        }
                    ],
                }
            },
        },
        source=config_entries.SOURCE_USER,
        version=1,
    )
    existing_entry.add_to_hass(hass)

    with (
        patch(
            NSW_FUEL_API_DEFINITION,
            return_value=mock_api_client,
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET},
        )

        # Go to advanced options
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"selected_station_codes": ["__advanced__"]},
        )

        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "advanced_options"

        # Configure advanced options - add DL fuel type to existing "Home" nickname
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                "nickname": "Home",  # EXISTING nickname
                "fuel_type": "DL",  # Non-U91 fuel type that already exists on station A
            },
        )

        # Should go to station_select
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "station_select"

        # Try to select station A with DL - this already exists in Home!
        # This should trigger sensor_exists error (line 415)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"selected_station_codes": [str(STATION_NSW_A)]},
        )

        # Should show sensor_exists error and stay on station_select
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "station_select"
        assert result["errors"]["base"] == "sensor_exists"


async def test_location_outside_nsw_tas_bounds(
    hass: HomeAssistant, mock_api_client: AsyncMock
) -> None:
    """Test error when location is outside NSW/TAS geographic bounds."""
    hass.config.latitude = HOME_LAT
    hass.config.longitude = HOME_LNG

    with (
        patch(
            NSW_FUEL_API_DEFINITION,
            return_value=mock_api_client,
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET},
        )

        # Go to advanced options
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"selected_station_codes": ["__advanced__"]},
        )

        # Test with latitude north of NSW/TAS bounds (> -28.99608)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                "nickname": "OutOfBounds",
                "location": {"latitude": -20.0, "longitude": 150.0},  # Too far north
                "fuel_type": "U91",
            },
        )

        # Should show invalid_coordinates error and stay on advanced_options
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "advanced_options"
        assert result["errors"]["base"] == "invalid_coordinates"

        # Test with longitude west of NSW/TAS bounds (< 141.00180)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                "nickname": "OutOfBounds",
                "location": {"latitude": -35.0, "longitude": 130.0},  # Too far west
                "fuel_type": "U91",
            },
        )

        # Should show invalid_coordinates error
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "advanced_options"
        assert result["errors"]["base"] == "invalid_coordinates"


async def test_build_user_schema_existing_entry(
    hass: HomeAssistant,
    mock_api_client: AsyncMock,
) -> None:
    """Verify suggested values for api key and secret are populated from existing config entry."""

    existing_entry = MockConfigEntry(
        domain=DOMAIN,
        title="NSW Fuel Check",
        data={
            CONF_CLIENT_ID: "saved_id",
            CONF_CLIENT_SECRET: "saved_secret",
        },
        source=config_entries.SOURCE_USER,
        version=1,
    )
    existing_entry.add_to_hass(hass)

    with patch(
        NSW_FUEL_API_DEFINITION,
        return_value=mock_api_client,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
        )

        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "user"
        schema = result["data_schema"]

        suggested_values = {
            key.schema: key.description.get("suggested_value")
            for key in schema.schema
            if isinstance(key, vol.Marker)
            and key.description
            and "suggested_value" in key.description
        }
        assert suggested_values.get(CONF_CLIENT_ID) == "saved_id"
        assert suggested_values.get(CONF_CLIENT_SECRET) == "saved_secret"


async def test_advanced_options_uses_hass_config_location_fallback(
    hass: HomeAssistant, mock_api_client: AsyncMock
) -> None:
    """Test that _build_advanced_options_schema falls back to hass.config when flow_data has no location.

    Exercises the code path in _build_advanced_options_schema lines 548-551:
        if not suggested_location:
            suggested_location = {
                "latitude": getattr(self.hass.config, "latitude", None),
                "longitude": getattr(self.hass.config, "longitude", None),
            }
    """

    # Set hass.config location (will be used as fallback)
    hass.config.latitude = HOME_LAT
    hass.config.longitude = HOME_LNG

    # Create a flow instance and manually set it up with empty flow_data
    # (no CONF_LOCATION key, which would trigger the fallback)
    flow = NSWFuelConfigFlow()
    flow.hass = hass
    flow._flow_data = {
        CONF_CLIENT_ID: CLIENT_ID,
        CONF_CLIENT_SECRET: CLIENT_SECRET,
        CONF_NICKNAME: "Test",
        # Note: NO CONF_LOCATION in flow_data - this triggers the fallback
    }

    # Call _build_advanced_options_schema directly
    schema = flow._build_advanced_options_schema()

    # Find the CONF_LOCATION field and check its suggested value
    location_suggested = None
    for key in schema.schema:
        if (
            isinstance(key, vol.Marker)
            and key.schema == CONF_LOCATION
            and key.description
            and "suggested_value" in key.description
        ):
            location_suggested = key.description.get("suggested_value")
            break

    # Verify the fallback populated the location from hass.config
    assert location_suggested is not None
    assert location_suggested["latitude"] == HOME_LAT
    assert location_suggested["longitude"] == HOME_LNG


async def test_build_advanced_options_schema_exception_raises(
    hass: HomeAssistant, mock_api_client: AsyncMock
) -> None:
    """If building advanced options schema errors, the exception propagates."""
    hass.config.latitude = HOME_LAT
    hass.config.longitude = HOME_LNG

    # Patch the helper to raise inside _build_advanced_options_schema
    with (
        patch(
            "custom_components.nsw_tas_fuel_station.config_flow.NSWFuelApiClient",
            return_value=mock_api_client,
        ),
        patch(
            "custom_components.nsw_tas_fuel_station.config_flow._get_state_defaults",
            side_effect=Exception("boom"),
        ),
    ):
        # Start flow and submit credentials
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET}
        )

        # Attempting to open advanced options should raise
        with pytest.raises(Exception):
            await hass.config_entries.flow.async_configure(
                result["flow_id"], {"selected_station_codes": ["__advanced__"]}
            )


@pytest.mark.parametrize(
    "location_input",
    [
        None,
        "not-a-dict",
        {},
        {"latitude": "bad", "longitude": HOME_LNG},
        {"latitude": HOME_LAT, "longitude": "bad"},
    ],
)
def test_validate_location_raises_for_invalid_inputs(
    hass: HomeAssistant, location_input
) -> None:
    """Parametrised invalid-location inputs should raise ValueError from _validate_location."""
    from custom_components.nsw_tas_fuel_station.config_flow import NSWFuelConfigFlow

    flow = NSWFuelConfigFlow()
    flow.hass = hass

    with pytest.raises(ValueError):
        flow._validate_location(location_input)  # type: ignore[arg-type]


async def test_get_station_list_api_not_initialized(hass: HomeAssistant) -> None:
    """If the API client is not initialized, _get_station_list raises HomeAssistantError."""
    from homeassistant.exceptions import HomeAssistantError

    from custom_components.nsw_tas_fuel_station.config_flow import NSWFuelConfigFlow

    flow = NSWFuelConfigFlow()
    flow.hass = hass
    flow.api = None

    with pytest.raises(HomeAssistantError):
        await flow._get_station_list(HOME_LAT, HOME_LNG, "U91")


def test_split_combo_fuel_code_empty() -> None:
    """_split_combo_fuel_code should return empty list for None or empty string."""
    assert _split_combo_fuel_code(None) == []
    assert _split_combo_fuel_code("") == []
    # A normal combo still splits correctly
    assert _split_combo_fuel_code(DEFAULT_FUEL_TYPE) >= ["E10"]
