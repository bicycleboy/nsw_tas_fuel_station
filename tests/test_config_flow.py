"""Tests for NSW Fuel Check config flow."""

from __future__ import annotations

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

from custom_components.nsw_tas_fuel_station.const import (
    CONF_FUEL_TYPE,
    CONF_LOCATION,
    CONF_NICKNAME,
    CONF_SELECTED_STATIONS,
    DEFAULT_NICKNAME,
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

NSW_FUEL_API_DEFINITION = (
    "custom_components.nsw_tas_fuel_station.config_flow.NSWFuelApiClient"
)


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

        # Verify fuel types stored on the station reflect API-observed fuels.
        assert sorted(station.get("fuel_types", [])) == sorted(expected_fuel_types)

        await hass.async_block_till_done()
        entries = hass.config_entries.async_entries(DOMAIN)
        assert len(entries) == 1
        assert entries[0].unique_id == CLIENT_ID


@pytest.mark.parametrize(
    ("existing", "select", "fuel", "expected", "expected_reason"),
    [
        pytest.param(
            {},
            [STATION_NSW_A],
            None,
            {STATION_NSW_A: ["E10", "U91"]},
            "nickname_created",
            id="new-nickname",
        ),
        pytest.param(
            {},
            [STATION_NSW_B],
            None,
            {STATION_NSW_B: ["U91"]},
            "nickname_created",
            id="new-nickname-combo-observed-fuels",
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
                CONF_LOCATION: {"latitude": HOME_LAT, "longitude": HOME_LNG},
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


async def test_config_flow_duplicate_entry(
    hass: HomeAssistant, mock_api_client: AsyncMock
):
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
    assert defaults.get(CONF_LOCATION) == user_location
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
