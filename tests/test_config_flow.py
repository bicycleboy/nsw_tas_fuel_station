"""Tests for NSW Fuel Check config flow."""

from __future__ import annotations

import copy
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_CLIENT_ID, CONF_CLIENT_SECRET
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr, entity_registry as er
from nsw_tas_fuel import NSWFuelApiClientAuthError, NSWFuelApiClientError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.nsw_tas_fuel_station.config_flow import _validate_location
from custom_components.nsw_tas_fuel_station.const import (
    CONF_CHEAPEST_FUEL_TYPE,
    CONF_EXCLUDE_STRING,
    CONF_FUEL_TYPE,
    CONF_LATITUDE,
    CONF_LOCATION,
    CONF_LONGITUDE,
    CONF_NICKNAME,
    CONF_RADIUS_KM,
    CONF_RADIUS_M,
    CONF_SELECTED_STATIONS,
    CONF_STATION_CODE,
    CONF_STATION_FUEL_TYPES,
    DEFAULT_NICKNAME,
    DOMAIN,
    LAT_SE_BOUND,
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

NSW_FUEL_API_DEFINITION = (
    "custom_components.nsw_tas_fuel_station.config_flow.NSWFuelApiClient"
)


def test_validate_location_rejects_out_of_bounds_coordinate() -> None:
    """Location validation rejects coordinates outside the supported area."""
    with pytest.raises(ValueError, match="invalid_coordinates"):
        _validate_location(
            {
                CONF_LATITUDE: LAT_SE_BOUND - 0.01,
                CONF_LONGITUDE: HOME_LNG,
            }
        )


async def test_invalid_home_location_uses_advanced_options(
    hass: HomeAssistant,
    mock_api_client: AsyncMock,
) -> None:
    """Invalid HA Home coordinates route the flow to advanced options."""
    hass.config.latitude = LAT_SE_BOUND - 0.01
    hass.config.longitude = HOME_LNG

    with patch(NSW_FUEL_API_DEFINITION, return_value=mock_api_client):
        result = await _start_flow_and_submit_creds(hass, CLIENT_ID, CLIENT_SECRET)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "advanced_options"
    assert result["errors"]["base"] == "invalid_coordinates"
    mock_api_client.get_fuel_prices_within_radius.assert_not_awaited()


@pytest.mark.parametrize(
    ("latitude", "longitude", "expected_state", "station_code", "expected_fuel_types"),
    [
        (HOME_LAT, HOME_LNG, "NSW", STATION_NSW_A, ["E10", "U91"]),
        (HOME_LAT, HOME_LNG, "NSW", STATION_NSW_B, ["U91"]),
        (HOME_LAT, HOME_LNG, "NSW", STATION_NSW_C, ["E10"]),
        (HOBART_LAT, HOBART_LNG, "TAS", STATION_TAS_D, ["U91"]),
        (HOBART_LAT, HOBART_LNG, "TAS", STATION_TAS_E, ["U91"]),
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
    expected_fuel_types: list[str],
) -> None:
    """Test successful config flow."""

    hass.config.latitude = latitude
    hass.config.longitude = longitude
    hass.config.time_zone = "Australia/Sydney"

    with patch(NSW_FUEL_API_DEFINITION, return_value=mock_api_client):
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
        assert home[CONF_CHEAPEST_FUEL_TYPE] == (
            "E10-U91" if expected_state == "NSW" else "U91"
        )

        # Verify fuel types stored on the station reflect API-observed fuels.
        assert sorted(station.get("fuel_types", [])) == sorted(expected_fuel_types)

        await hass.async_block_till_done()
        entries = hass.config_entries.async_entries(DOMAIN)
        assert len(entries) == 1
        assert entries[0].unique_id == CLIENT_ID


@pytest.mark.parametrize(
    (
        "existing",
        "select",
        "fuel",
        "expected_cheapest_fuel_type",
        "radius_meters",
        "expected_radius_km",
        "expected",
        "expected_reason",
    ),
    [
        pytest.param(
            {},
            [STATION_NSW_A],
            None,
            "E10-U91",
            25_000,
            25,
            {STATION_NSW_A: ["E10", "U91"]},
            "nickname_created",
            id="new-nickname",
        ),
        pytest.param(
            {},
            [STATION_NSW_B],
            None,
            "E10-U91",
            25_001,
            26,
            {STATION_NSW_B: ["U91"]},
            "nickname_created",
            id="new-nickname-combo-observed-fuels-radius-round-up",
        ),
        pytest.param(
            {
                "nicknames": {
                    DEFAULT_NICKNAME: {
                        "location": {"latitude": HOME_LAT, "longitude": HOME_LNG},
                        "stations": [
                            {
                                "station_code": STATION_NSW_A,
                                "station_name": "A",
                                "au_state": "NSW",
                                "fuel_types": ["E10", "U91"],
                            }
                        ],
                    }
                }
            },
            [STATION_NSW_B],
            None,
            "E10-U91",
            10_001,
            11,
            {
                STATION_NSW_A: ["E10", "U91"],
                STATION_NSW_B: ["U91"],
            },
            "reconfigured",
            id="add-station",
        ),
        pytest.param(
            {
                "nicknames": {
                    DEFAULT_NICKNAME: {
                        "location": {"latitude": HOME_LAT, "longitude": HOME_LNG},
                        "stations": [
                            {
                                "station_code": STATION_NSW_A,
                                "station_name": "A",
                                "au_state": "NSW",
                                "fuel_types": ["E10"],
                            },
                            {
                                "station_code": STATION_NSW_B,
                                "station_name": "B",
                                "au_state": "NSW",
                                "fuel_types": ["U91"],
                            },
                        ],
                    }
                }
            },
            [STATION_NSW_A],
            "DL",
            "DL",
            5_100,
            6,
            {
                STATION_NSW_A: ["DL", "E10"],
                STATION_NSW_B: ["U91"],
            },
            "reconfigured",
            id="add-fuel-multi",
        ),
    ],
)
async def test_successful_reconfigure_flow(
    hass: HomeAssistant,
    mock_api_client: AsyncMock,
    existing: dict[str, Any],
    select: list[int],
    fuel: str | None,
    expected_cheapest_fuel_type: str,
    radius_meters: float,
    expected_radius_km: int,
    expected: dict[int, list[str]],
    expected_reason: str,
) -> None:
    """Test successful reconfigure flow."""

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=CLIENT_ID,
        data={
            CONF_CLIENT_ID: CLIENT_ID,
            CONF_CLIENT_SECRET: CLIENT_SECRET,
            **existing,
        },
    )
    entry.add_to_hass(hass)

    hass.config.latitude = HOME_LAT
    hass.config.longitude = HOME_LNG
    hass.config.time_zone = "Australia/Sydney"

    with patch(NSW_FUEL_API_DEFINITION, return_value=mock_api_client):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": config_entries.SOURCE_RECONFIGURE,
                "entry_id": entry.entry_id,
            },
        )

        assert result["step_id"] == "advanced_options"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_NICKNAME: DEFAULT_NICKNAME,
                CONF_LOCATION: {
                    "latitude": HOME_LAT,
                    "longitude": HOME_LNG,
                    CONF_RADIUS_M: radius_meters,
                },
                CONF_FUEL_TYPE: fuel or "E10-U91",
            },
        )

        assert result["step_id"] == "station_select"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_SELECTED_STATIONS: [str(s) for s in select]},
        )

        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == expected_reason

    updated = hass.config_entries.async_get_entry(entry.entry_id)

    assert get_station_map(updated.data) == {
        code: sorted(fuels) for code, fuels in expected.items()
    }

    nickname_data = updated.data["nicknames"][DEFAULT_NICKNAME]
    assert nickname_data["radius_km"] == expected_radius_km
    assert nickname_data[CONF_CHEAPEST_FUEL_TYPE] == expected_cheapest_fuel_type

    assert mock_api_client.get_fuel_prices_within_radius.await_args
    assert (
        mock_api_client.get_fuel_prices_within_radius.await_args.kwargs["radius"]
        == expected_radius_km
    )


async def test_config_flow_duplicate_entry(
    hass: HomeAssistant, mock_api_client: AsyncMock
) -> None:
    """Enforce one config entry per integration."""

    # Add existing config entry with same client_id
    existing_entry = MockConfigEntry(
        domain=DOMAIN,
        title="NSW Fuel Check",
        unique_id=CLIENT_ID,
        data={
            CONF_CLIENT_ID: CLIENT_ID,
            CONF_CLIENT_SECRET: CLIENT_SECRET,
        },
    )
    existing_entry.add_to_hass(hass)

    hass.config.latitude = HOME_LAT
    hass.config.longitude = HOME_LNG

    with patch(NSW_FUEL_API_DEFINITION, return_value=mock_api_client):
        # Start a new config flow with the same client_id
        result = await _start_flow_and_submit_creds(hass, CLIENT_ID, CLIENT_SECRET)

    # The flow should abort because the client_id is already configured
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"


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


async def test_advanced_options_no_station_results(
    hass: HomeAssistant,
) -> None:
    """Test advanced options returns to same step when API returns no stations."""

    no_stations_client = AsyncMock()
    no_stations_client.get_fuel_prices_within_radius = AsyncMock(return_value=[])

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=CLIENT_ID,
        data={
            CONF_CLIENT_ID: CLIENT_ID,
            CONF_CLIENT_SECRET: CLIENT_SECRET,
            "nicknames": {
                DEFAULT_NICKNAME: {
                    "location": {"latitude": HOME_LAT, "longitude": HOME_LNG},
                    "stations": [
                        {
                            "station_code": STATION_NSW_A,
                            "station_name": "A",
                            "au_state": "NSW",
                            "fuel_types": ["U91"],
                        }
                    ],
                }
            },
        },
    )
    entry.add_to_hass(hass)

    hass.config.latitude = HOME_LAT
    hass.config.longitude = HOME_LNG
    hass.config.time_zone = "Australia/Sydney"

    with patch(NSW_FUEL_API_DEFINITION, return_value=no_stations_client):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": config_entries.SOURCE_RECONFIGURE,
                "entry_id": entry.entry_id,
            },
        )
        assert result["step_id"] == "advanced_options"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_NICKNAME: DEFAULT_NICKNAME,
                CONF_LOCATION: {"latitude": HOBART_LAT, "longitude": HOBART_LNG},
                CONF_FUEL_TYPE: "U91",
            },
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "advanced_options"
    assert result["errors"]["base"] == "no_stations"


@pytest.mark.parametrize(
    (
        "exception_class",
        "error_message",
        "expected_error_key",
        "expected_step_id",
        "returns_no_stations",
    ),
    [
        (
            NSWFuelApiClientAuthError,
            "Invalid credentials",
            "auth",
            "user",
            False,
        ),
        (
            NSWFuelApiClientError,
            "Request timeout (408)",
            "connection",
            "user",
            False,
        ),
        (
            NSWFuelApiClientError,
            "Bad request (400)",
            "connection",
            "user",
            False,
        ),
        (
            NSWFuelApiClientError,
            "Internal server error (500)",
            "connection",
            "user",
            False,
        ),
        (None, "", "no_stations", "advanced_options", True),
    ],
    ids=[
        "auth-invalid-credentials",
        "connection-timeout-408",
        "connection-bad-request-400",
        "connection-server-error-500",
        "no-stations-empty-response",
    ],
)
async def test_errors_on_station_fetch(
    hass_with_config: HomeAssistant,
    exception_class: type | None,
    error_message: str,
    expected_error_key: str,
    expected_step_id: str,
    returns_no_stations: bool,
) -> None:
    """Test station fetch outcomes (API errors and no stations) in user flow."""
    error_client = AsyncMock()
    if returns_no_stations:
        error_client.get_fuel_prices_within_radius = AsyncMock(return_value=[])
    else:
        error_client.get_fuel_prices_within_radius = AsyncMock(
            side_effect=exception_class(error_message)
        )

    with patch(NSW_FUEL_API_DEFINITION, return_value=error_client):
        result = await _start_flow_and_submit_creds(
            hass_with_config, CLIENT_ID, CLIENT_SECRET
        )

        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == expected_step_id
        assert "base" in result["errors"]
        assert result["errors"]["base"] == expected_error_key


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


@pytest.mark.parametrize(
    ("nickname", "api_side_effect", "expected_error"),
    [
        pytest.param("", None, None, id="empty-nickname"),
        pytest.param("bad<>name", None, None, id="invalid-chars-nickname"),
        pytest.param(
            DEFAULT_NICKNAME,
            NSWFuelApiClientError("Internal server error (500)"),
            "connection",
            id="api-error",
        ),
    ],
)
async def test_advanced_options_preserves_user_location_and_fuel_on_error(
    hass: HomeAssistant,
    mock_api_client: AsyncMock,
    nickname: str,
    api_side_effect: Exception | None,
    expected_error: str | None,
) -> None:
    """Ensure advanced options keeps entered location/fuel on validation or API errors."""

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=CLIENT_ID,
        data={
            CONF_CLIENT_ID: CLIENT_ID,
            CONF_CLIENT_SECRET: CLIENT_SECRET,
            "nicknames": {
                DEFAULT_NICKNAME: {
                    "location": {"latitude": HOME_LAT, "longitude": HOME_LNG},
                    "stations": [
                        {
                            "station_code": STATION_NSW_A,
                            "station_name": "A",
                            "au_state": "NSW",
                            "fuel_types": ["U91"],
                        }
                    ],
                }
            },
        },
    )
    entry.add_to_hass(hass)

    hass.config.latitude = HOME_LAT
    hass.config.longitude = HOME_LNG
    hass.config.time_zone = "Australia/Sydney"

    if api_side_effect is not None:
        mock_api_client.get_fuel_prices_within_radius.side_effect = api_side_effect

    with patch(NSW_FUEL_API_DEFINITION, return_value=mock_api_client):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": config_entries.SOURCE_RECONFIGURE,
                "entry_id": entry.entry_id,
            },
        )
        assert result["step_id"] == "advanced_options"

        user_location = {"latitude": HOBART_LAT, "longitude": HOBART_LNG}
        user_fuel = "U91"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_NICKNAME: nickname,
                CONF_LOCATION: user_location,
                CONF_FUEL_TYPE: user_fuel,
            },
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "advanced_options"
    if expected_error is not None:
        assert result["errors"]["base"] == expected_error

    schema = result["data_schema"]
    defaults = {
        key.schema: key.default() if callable(key.default) else key.default
        for key in schema.schema
        if isinstance(key, vol.Marker) and key.default is not vol.UNDEFINED
    }
    assert defaults.get(CONF_LOCATION) is not None
    assert defaults[CONF_LOCATION]["latitude"] == user_location["latitude"]
    assert defaults[CONF_LOCATION]["longitude"] == user_location["longitude"]
    assert defaults.get(CONF_FUEL_TYPE) == user_fuel


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


def get_station_map(entry_data: dict) -> dict[int, list[str]]:
    """Return {station_code: sorted fuel list} for the 'home' nickname."""
    stations = entry_data["nicknames"][DEFAULT_NICKNAME]["stations"]
    return {s["station_code"]: sorted(s["fuel_types"]) for s in stations}


async def test_manage_station_removal_is_nickname_scoped(
    hass: HomeAssistant,
) -> None:
    """Removing a station from one nickname leaves another nickname untouched."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=CLIENT_ID,
        data={
            CONF_CLIENT_ID: CLIENT_ID,
            CONF_CLIENT_SECRET: CLIENT_SECRET,
            "nicknames": {
                "Home": {
                    "stations": [
                        {
                            "station_code": STATION_NSW_A,
                            "station_name": "Station A",
                            "au_state": "NSW",
                            "fuel_types": ["E10", "U91"],
                        },
                        {
                            "station_code": STATION_NSW_B,
                            "station_name": "Station B",
                            "au_state": "NSW",
                            "fuel_types": ["U91"],
                        },
                    ]
                },
                "Petrol": {
                    "stations": [
                        {
                            "station_code": STATION_NSW_A,
                            "station_name": "Station A",
                            "au_state": "NSW",
                            "fuel_types": ["U91"],
                        }
                    ]
                },
            },
        },
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": config_entries.SOURCE_RECONFIGURE,
                "entry_id": entry.entry_id,
            },
        )
        assert result["type"] is FlowResultType.MENU
        assert result["step_id"] == "reconfigure"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "manage_stations"}
        )
        assert result["step_id"] == "manage_stations"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_NICKNAME: "Home"}
        )
        assert result["step_id"] == "manage_station"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_STATION_CODE: str(STATION_NSW_A)}
        )
        assert result["step_id"] == "edit_station"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_STATION_FUEL_TYPES: [],
                "remove_station": True,
            },
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "manage_station"
    assert len(entry.data["nicknames"]["Home"]["stations"]) == 1
    assert (
        entry.data["nicknames"]["Home"]["stations"][0]["station_code"]
        == STATION_NSW_B
    )
    assert len(entry.data["nicknames"]["Petrol"]["stations"]) == 1
    assert (
        entry.data["nicknames"]["Petrol"]["stations"][0]["station_code"]
        == STATION_NSW_A
    )


async def test_manage_station_edits_fuels_without_removing_station(
    hass: HomeAssistant,
) -> None:
    """Editing fuels replaces only the selected station's configured fuel list."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=CLIENT_ID,
        data={
            CONF_CLIENT_ID: CLIENT_ID,
            CONF_CLIENT_SECRET: CLIENT_SECRET,
            "nicknames": {
                "Home": {
                    "stations": [
                        {
                            "station_code": STATION_NSW_A,
                            "station_name": "Station A",
                            "au_state": "NSW",
                            "fuel_types": ["E10", "U91"],
                        },
                        {
                            "station_code": STATION_NSW_B,
                            "station_name": "Station B",
                            "au_state": "NSW",
                            "fuel_types": ["U91"],
                        },
                    ]
                }
            },
        },
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": config_entries.SOURCE_RECONFIGURE,
                "entry_id": entry.entry_id,
            },
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "manage_stations"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_NICKNAME: "Home"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_STATION_CODE: str(STATION_NSW_A)}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_STATION_FUEL_TYPES: ["U91"],
                "remove_station": False,
            },
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "manage_station"
    stations = entry.data["nicknames"]["Home"]["stations"]
    assert len(stations) == 2
    assert stations[0]["fuel_types"] == ["U91"]
    assert stations[1]["fuel_types"] == ["U91"]


async def test_manage_station_rejects_empty_fuels_without_removal(
    hass: HomeAssistant,
) -> None:
    """A station cannot be left configured with no fuels accidentally."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=CLIENT_ID,
        data={
            CONF_CLIENT_ID: CLIENT_ID,
            CONF_CLIENT_SECRET: CLIENT_SECRET,
            "nicknames": {
                "Home": {
                    "stations": [
                        {
                            "station_code": STATION_NSW_A,
                            "station_name": "Station A",
                            "au_state": "NSW",
                            "fuel_types": ["U91"],
                        }
                    ]
                }
            },
        },
    )
    entry.add_to_hass(hass)
    original_data = dict(entry.data)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "entry_id": entry.entry_id,
        },
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "manage_stations"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_NICKNAME: "Home"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_STATION_CODE: str(STATION_NSW_A)}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_STATION_FUEL_TYPES: [],
            "remove_station": False,
        },
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "edit_station"
    assert result["errors"]["base"] == "select_fuel_or_remove_station"
    assert entry.data == original_data


async def test_manage_station_offers_station_reported_fuels(
    hass: HomeAssistant,
    mock_api_client: AsyncMock,
) -> None:
    """Editing a station offers currently reported fuels, not only configured fuels."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=CLIENT_ID,
        data={
            CONF_CLIENT_ID: CLIENT_ID,
            CONF_CLIENT_SECRET: CLIENT_SECRET,
            "nicknames": {
                "Home": {
                    "stations": [
                        {
                            "station_code": STATION_NSW_A,
                            "station_name": "Station A",
                            "au_state": "NSW",
                            "fuel_types": ["U91"],
                        }
                    ]
                }
            },
        },
    )
    entry.add_to_hass(hass)

    with patch(NSW_FUEL_API_DEFINITION, return_value=mock_api_client):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": config_entries.SOURCE_RECONFIGURE,
                "entry_id": entry.entry_id,
            },
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "manage_stations"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_NICKNAME: "Home"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_STATION_CODE: str(STATION_NSW_A)}
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "edit_station"

    selector = result["data_schema"].schema[
        next(
            key
            for key in result["data_schema"].schema
            if isinstance(key, vol.Marker)
            and key.schema == CONF_STATION_FUEL_TYPES
        )
    ]
    option_values = {option["value"] for option in selector.config["options"]}
    assert option_values == {"DL", "E10", "U91"}
    mock_api_client.get_fuel_prices_for_station.assert_awaited_once_with(
        str(STATION_NSW_A), "NSW"
    )


async def test_manage_station_uses_device_user_name_for_nickname_label(
    hass: HomeAssistant,
) -> None:
    """Reconfigure shows a renamed HA device while preserving stored nickname value."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=CLIENT_ID,
        data={
            CONF_CLIENT_ID: CLIENT_ID,
            CONF_CLIENT_SECRET: CLIENT_SECRET,
            "nicknames": {
                "Home": {
                    "stations": [
                        {
                            "station_code": STATION_NSW_A,
                            "station_name": "Station A",
                            "au_state": "NSW",
                            "fuel_types": ["U91"],
                        }
                    ]
                }
            },
        },
    )
    entry.add_to_hass(hass)

    device_registry = dr.async_get(hass)
    device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "location_Home")},
        name="Home",
    )
    device_registry.async_update_device(device.id, name_by_user="Diesel")

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "entry_id": entry.entry_id,
        },
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "manage_stations"}
    )

    selector = result["data_schema"].schema[
        next(
            key
            for key in result["data_schema"].schema
            if isinstance(key, vol.Marker) and key.schema == CONF_NICKNAME
        )
    ]
    assert selector.config["options"] == [{"value": "Home", "label": "Diesel"}]


async def test_manage_station_removes_stale_entity_registry_entry(
    hass: HomeAssistant,
    mock_api_client: AsyncMock,
) -> None:
    """Removing a configured station also removes its registry entity."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=CLIENT_ID,
        data={
            CONF_CLIENT_ID: CLIENT_ID,
            CONF_CLIENT_SECRET: CLIENT_SECRET,
            "nicknames": {
                "Home": {
                    "stations": [
                        {
                            "station_code": STATION_NSW_A,
                            "station_name": "Station A",
                            "au_state": "NSW",
                            "fuel_types": ["U91"],
                        },
                        {
                            "station_code": STATION_NSW_B,
                            "station_name": "Station B",
                            "au_state": "NSW",
                            "fuel_types": ["U91"],
                        }
                    ]
                }
            },
        },
    )
    entry.add_to_hass(hass)

    device_registry = dr.async_get(hass)
    device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "location_Home")},
        name="Home",
    )
    entity_registry = er.async_get(hass)
    entity = entity_registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{DOMAIN}_Home_{STATION_NSW_A}_NSW_U91",
        config_entry=entry,
        device_id=device.id,
        suggested_object_id="station_a_u91",
    )

    with patch(NSW_FUEL_API_DEFINITION, return_value=mock_api_client):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": config_entries.SOURCE_RECONFIGURE,
                "entry_id": entry.entry_id,
            },
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "manage_stations"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_NICKNAME: "Home"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_STATION_CODE: str(STATION_NSW_A)}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_STATION_FUEL_TYPES: [],
                "remove_station": True,
            },
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "manage_station"
    assert entity_registry.async_get(entity.entity_id) is None


async def test_edit_existing_location_updates_settings_without_station_selection(
    hass: HomeAssistant,
) -> None:
    """Existing location settings can be changed without selecting stations."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=CLIENT_ID,
        data={
            CONF_CLIENT_ID: CLIENT_ID,
            CONF_CLIENT_SECRET: CLIENT_SECRET,
            "nicknames": {
                "Home": {
                    CONF_LOCATION: {
                        CONF_LATITUDE: HOME_LAT,
                        CONF_LONGITUDE: HOME_LNG,
                    },
                    CONF_RADIUS_KM: 10,
                    CONF_CHEAPEST_FUEL_TYPE: "U91",
                    CONF_EXCLUDE_STRING: "",
                    "stations": [
                        {
                            "station_code": STATION_NSW_A,
                            "station_name": "Station A",
                            "au_state": "NSW",
                            "fuel_types": ["U91"],
                        }
                    ],
                }
            },
        },
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": config_entries.SOURCE_RECONFIGURE,
                "entry_id": entry.entry_id,
            },
        )
        assert result["type"] is FlowResultType.MENU

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "edit_location"}
        )
        assert result["step_id"] == "edit_location"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_NICKNAME: "Home"}
        )
        assert result["step_id"] == "edit_location_settings"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_LOCATION: {
                    CONF_LATITUDE: HOME_LAT,
                    CONF_LONGITUDE: HOME_LNG,
                    CONF_RADIUS_M: 15_500,
                },
                CONF_FUEL_TYPE: "P95",
                CONF_EXCLUDE_STRING: "Members only",
            },
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "location_updated"
    home = entry.data["nicknames"]["Home"]
    assert home[CONF_RADIUS_KM] == 16
    assert home[CONF_CHEAPEST_FUEL_TYPE] == "P95"
    assert home[CONF_EXCLUDE_STRING] == "Members only"
    assert len(home["stations"]) == 1


async def test_last_station_requires_confirmation_then_removes_location(
    hass: HomeAssistant,
    mock_api_client: AsyncMock,
) -> None:
    """Removing a location's final station requires confirmation and removes device."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=CLIENT_ID,
        data={
            CONF_CLIENT_ID: CLIENT_ID,
            CONF_CLIENT_SECRET: CLIENT_SECRET,
            "nicknames": {
                "Home": {
                    "stations": [
                        {
                            "station_code": STATION_NSW_A,
                            "station_name": "Station A",
                            "au_state": "NSW",
                            "fuel_types": ["U91"],
                        }
                    ]
                },
                "Petrol": {
                    "stations": [
                        {
                            "station_code": STATION_NSW_B,
                            "station_name": "Station B",
                            "au_state": "NSW",
                            "fuel_types": ["U91"],
                        }
                    ]
                },
            },
        },
    )
    entry.add_to_hass(hass)

    device_registry = dr.async_get(hass)
    device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "location_Home")},
        name="Home",
    )
    entity_registry = er.async_get(hass)
    favorite = entity_registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{DOMAIN}_Home_{STATION_NSW_A}_NSW_U91",
        config_entry=entry,
        device_id=device.id,
        suggested_object_id="home_station_a_u91",
    )
    cheapest = entity_registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{DOMAIN}_cheapest_Home_1",
        config_entry=entry,
        device_id=device.id,
        suggested_object_id="cheapest_home_1",
    )

    with (
        patch(NSW_FUEL_API_DEFINITION, return_value=mock_api_client),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": config_entries.SOURCE_RECONFIGURE,
                "entry_id": entry.entry_id,
            },
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "manage_stations"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_NICKNAME: "Home"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_STATION_CODE: str(STATION_NSW_A)}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_STATION_FUEL_TYPES: [],
                "remove_station": True,
            },
        )

        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "confirm_remove_empty_location"
        assert "Home" in entry.data["nicknames"]

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {}
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "location_removed"
    assert "Home" not in entry.data["nicknames"]
    assert "Petrol" in entry.data["nicknames"]
    assert entity_registry.async_get(favorite.entity_id) is None
    assert entity_registry.async_get(cheapest.entity_id) is None
    assert device_registry.async_get(device.id) is None


async def test_edit_location_rejects_cheapest_fuel_with_no_prices(
    hass: HomeAssistant,
    mock_api_client: AsyncMock,
) -> None:
    """Do not save location settings when FuelCheck returns no matching prices."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=CLIENT_ID,
        data={
            CONF_CLIENT_ID: CLIENT_ID,
            CONF_CLIENT_SECRET: CLIENT_SECRET,
            "nicknames": {
                "Home": {
                    CONF_LOCATION: {
                        CONF_LATITUDE: HOME_LAT,
                        CONF_LONGITUDE: HOME_LNG,
                    },
                    CONF_RADIUS_KM: 10,
                    CONF_CHEAPEST_FUEL_TYPE: "U91",
                    CONF_EXCLUDE_STRING: "",
                    "stations": [
                        {
                            "station_code": STATION_NSW_A,
                            "station_name": "Station A",
                            "au_state": "NSW",
                            "fuel_types": ["U91"],
                        }
                    ],
                }
            },
        },
    )
    entry.add_to_hass(hass)
    original_data = copy.deepcopy(dict(entry.data))
    mock_api_client.get_fuel_prices_within_radius = AsyncMock(return_value=[])

    with patch(NSW_FUEL_API_DEFINITION, return_value=mock_api_client):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": config_entries.SOURCE_RECONFIGURE,
                "entry_id": entry.entry_id,
            },
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "edit_location"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_NICKNAME: "Home"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_LOCATION: {
                    CONF_LATITUDE: HOME_LAT,
                    CONF_LONGITUDE: HOME_LNG,
                    CONF_RADIUS_M: 10_000,
                },
                CONF_FUEL_TYPE: "H2",
                CONF_EXCLUDE_STRING: "",
            },
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "edit_location_settings"
    assert result["errors"]["base"] == "no_prices_for_location"
    assert entry.data == original_data


async def test_delete_location_with_no_stations(
    hass: HomeAssistant,
) -> None:
    """An empty location can still be explicitly deleted."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=CLIENT_ID,
        data={
            CONF_CLIENT_ID: CLIENT_ID,
            CONF_CLIENT_SECRET: CLIENT_SECRET,
            "nicknames": {
                "Empty": {
                    CONF_LOCATION: {
                        CONF_LATITUDE: HOME_LAT,
                        CONF_LONGITUDE: HOME_LNG,
                    },
                    CONF_RADIUS_KM: 10,
                    CONF_CHEAPEST_FUEL_TYPE: "U91",
                    CONF_EXCLUDE_STRING: "",
                    "stations": [],
                },
                "Petrol": {
                    "stations": [
                        {
                            "station_code": STATION_NSW_A,
                            "station_name": "Station A",
                            "au_state": "NSW",
                            "fuel_types": ["U91"],
                        }
                    ]
                },
            },
        },
    )
    entry.add_to_hass(hass)

    device_registry = dr.async_get(hass)
    device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "location_Empty")},
        name="Empty",
    )
    entity_registry = er.async_get(hass)
    cheapest = entity_registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{DOMAIN}_cheapest_Empty_1",
        config_entry=entry,
        device_id=device.id,
        suggested_object_id="cheapest_empty_1",
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "entry_id": entry.entry_id,
        },
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "delete_location"}
    )
    assert result["step_id"] == "delete_location"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_NICKNAME: "Empty"}
    )
    assert result["step_id"] == "confirm_delete_location"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "location_removed"
    assert "Empty" not in entry.data["nicknames"]
    assert "Petrol" in entry.data["nicknames"]
    assert entity_registry.async_get(cheapest.entity_id) is None
    assert device_registry.async_get(device.id) is None


async def test_add_station_to_existing_location_uses_stored_nickname(
    hass: HomeAssistant,
    mock_api_client: AsyncMock,
) -> None:
    """A renamed device adds stations to its stored nickname without creating a duplicate."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=CLIENT_ID,
        data={
            CONF_CLIENT_ID: CLIENT_ID,
            CONF_CLIENT_SECRET: CLIENT_SECRET,
            "nicknames": {
                "Home": {
                    CONF_LOCATION: {
                        CONF_LATITUDE: HOME_LAT,
                        CONF_LONGITUDE: HOME_LNG,
                    },
                    CONF_RADIUS_KM: 10,
                    CONF_CHEAPEST_FUEL_TYPE: "U91",
                    CONF_EXCLUDE_STRING: "Members only",
                    "stations": [
                        {
                            "station_code": STATION_NSW_A,
                            "station_name": "Station A",
                            "au_state": "NSW",
                            "fuel_types": ["U91"],
                        }
                    ],
                }
            },
        },
    )
    entry.add_to_hass(hass)

    device_registry = dr.async_get(hass)
    device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "location_Home")},
        name="Home",
    )
    device_registry.async_update_device(device.id, name_by_user="Petrol")

    with patch(NSW_FUEL_API_DEFINITION, return_value=mock_api_client):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": config_entries.SOURCE_RECONFIGURE,
                "entry_id": entry.entry_id,
            },
        )
        assert result["type"] is FlowResultType.MENU

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "add_station_existing"}
        )
        assert result["step_id"] == "add_station_existing"

        selector = result["data_schema"].schema[
            next(
                key
                for key in result["data_schema"].schema
                if isinstance(key, vol.Marker) and key.schema == CONF_NICKNAME
            )
        ]
        assert selector.config["options"] == [{"value": "Home", "label": "Petrol"}]

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_NICKNAME: "Home"}
        )
        assert result["step_id"] == "add_station_search"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_LOCATION: {
                    CONF_LATITUDE: HOME_LAT,
                    CONF_LONGITUDE: HOME_LNG,
                    CONF_RADIUS_M: 20_000,
                },
                CONF_FUEL_TYPE: "U91",
            },
        )
        assert result["step_id"] == "station_select"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_SELECTED_STATIONS: [str(STATION_NSW_B)]},
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "stations_added"
    assert set(entry.data["nicknames"]) == {"Home"}

    home = entry.data["nicknames"]["Home"]
    assert {station["station_code"] for station in home["stations"]} == {
        STATION_NSW_A,
        STATION_NSW_B,
    }
    assert home[CONF_LOCATION] == {
        CONF_LATITUDE: HOME_LAT,
        CONF_LONGITUDE: HOME_LNG,
    }
    assert home[CONF_RADIUS_KM] == 10
    assert home[CONF_CHEAPEST_FUEL_TYPE] == "U91"
    assert home[CONF_EXCLUDE_STRING] == "Members only"
