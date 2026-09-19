"""Tests for NSW Fuel Check integration setup."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.const import CONF_CLIENT_ID, CONF_CLIENT_SECRET
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from custom_components.nsw_tas_fuel_station import async_setup_entry
from custom_components.nsw_tas_fuel_station.const import DOMAIN


async def test_setup_entry_refreshes_coordinator_once(hass: HomeAssistant) -> None:
    """Integration setup performs one initial refresh before forwarding platforms."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_CLIENT_ID: "test_client_id",
            CONF_CLIENT_SECRET: "test_client_secret",
            "nicknames": {},
        },
    )
    entry.add_to_hass(hass)

    coordinator = MagicMock()
    coordinator.async_config_entry_first_refresh = AsyncMock()

    with (
        patch("custom_components.nsw_tas_fuel_station.NSWFuelApiClient"),
        patch(
            "custom_components.nsw_tas_fuel_station.NSWFuelCoordinator",
            return_value=coordinator,
        ),
        patch.object(
            hass.config_entries,
            "async_forward_entry_setups",
            new=AsyncMock(),
        ) as forward_setups,
    ):
        assert await async_setup_entry(hass, entry)

    coordinator.async_config_entry_first_refresh.assert_awaited_once_with()
    forward_setups.assert_awaited_once()


async def test_setup_entry_refresh_failure_does_not_forward_platforms(
    hass: HomeAssistant,
) -> None:
    """A failed integration-level first refresh prevents platform setup."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_CLIENT_ID: "test_client_id",
            CONF_CLIENT_SECRET: "test_client_secret",
            "nicknames": {},
        },
    )
    entry.add_to_hass(hass)

    coordinator = MagicMock()
    coordinator.async_config_entry_first_refresh = AsyncMock(
        side_effect=ConfigEntryNotReady("initial refresh failed")
    )

    with (
        patch(
            "custom_components.nsw_tas_fuel_station.NSWFuelApiClient"
        ),
        patch(
            "custom_components.nsw_tas_fuel_station.NSWFuelCoordinator",
            return_value=coordinator,
        ),
        patch.object(
            hass.config_entries,
            "async_forward_entry_setups",
            new=AsyncMock(),
        ) as forward_setups,
        pytest.raises(ConfigEntryNotReady),
    ):
        await async_setup_entry(hass, entry)

    coordinator.async_config_entry_first_refresh.assert_awaited_once_with()
    forward_setups.assert_not_awaited()
