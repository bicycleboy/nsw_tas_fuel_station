"""Tests for NSW Fuel Check diagnostics."""

from datetime import timedelta

import pytest
from homeassistant.core import HomeAssistant

from custom_components.nsw_tas_fuel_station.const import DOMAIN
from custom_components.nsw_tas_fuel_station.coordinator import NSWFuelCoordinator
from custom_components.nsw_tas_fuel_station.diagnostics import (
    async_get_config_entry_diagnostics,
)


@pytest.fixture
def coordinator(
    hass: HomeAssistant,
    mock_api_client,
    mock_config_entry,
) -> NSWFuelCoordinator:
    """Return a coordinator using the standard mocked API client."""
    return NSWFuelCoordinator(
        hass,
        mock_api_client,
        mock_config_entry.data["nicknames"],
        timedelta(minutes=30),
    )


async def test_diagnostics_with_client_http_accounting(
    hass: HomeAssistant,
    mock_config_entry,
    coordinator: NSWFuelCoordinator,
) -> None:
    """Diagnostics expose operation and HTTP request accounting."""
    coordinator._last_refresh_api_operations = {
        "favorite_station": 10,
        "cheapest_nearby": 2,
    }
    coordinator._api_operations_total = 36
    coordinator.api.http_request_counts = {
        "oauth": 1,
        "data": 15,
        "retries": 3,
    }
    coordinator.api.last_token_expires_in = 43199

    hass.data.setdefault(DOMAIN, {})[mock_config_entry.entry_id] = coordinator

    result = await async_get_config_entry_diagnostics(hass, mock_config_entry)
    accounting = result["api_request_accounting"]

    assert accounting["last_refresh_api_operations"] == {
        "favorite_station": 10,
        "cheapest_nearby": 2,
    }
    assert accounting["session_api_operations_total"] == 36
    assert accounting["http_request_counts"] == {
        "oauth": 1,
        "data": 15,
        "retries": 3,
    }
    # Data already includes retried HTTP attempts; do not add retries again.
    assert accounting["session_http_requests_total"] == 16
    assert accounting["last_token_expires_in"] == 43199


async def test_diagnostics_without_optional_client_http_accounting(
    hass: HomeAssistant,
    mock_config_entry,
    coordinator: NSWFuelCoordinator,
) -> None:
    """Diagnostics remain usable with a client lacking HTTP counters."""
    coordinator._last_refresh_api_operations = {
        "favorite_station": 1,
        "cheapest_nearby": 1,
    }
    coordinator._api_operations_total = 2

    if hasattr(coordinator.api, "http_request_counts"):
        del coordinator.api.http_request_counts
    if hasattr(coordinator.api, "last_token_expires_in"):
        del coordinator.api.last_token_expires_in

    hass.data.setdefault(DOMAIN, {})[mock_config_entry.entry_id] = coordinator

    result = await async_get_config_entry_diagnostics(hass, mock_config_entry)
    accounting = result["api_request_accounting"]

    assert accounting["last_refresh_api_operations"] == {
        "favorite_station": 1,
        "cheapest_nearby": 1,
    }
    assert accounting["session_api_operations_total"] == 2
    assert accounting["http_request_counts"] is None
    assert accounting["session_http_requests_total"] is None
    assert accounting["last_token_expires_in"] is None
