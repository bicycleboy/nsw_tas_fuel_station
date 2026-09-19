"""Tests for NSW Fuel Check integration setup and removal."""

from __future__ import annotations

from copy import deepcopy

from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from custom_components.nsw_tas_fuel_station import async_remove_config_entry_device
from custom_components.nsw_tas_fuel_station.const import DOMAIN


def _entry_data() -> dict:
    """Return config entry data with two independent nicknames."""
    return {
        "client_id": "test_client_id",
        "client_secret": "test_client_secret",
        "nicknames": {
            "Home": {
                "location": {"latitude": -35.28, "longitude": 149.13},
                "stations": [
                    {
                        "station_code": 111,
                        "station_name": "Home Station",
                        "au_state": "NSW",
                        "fuel_types": ["U91", "E10"],
                    }
                ],
            },
            "Work": {
                "location": {"latitude": -35.30, "longitude": 149.10},
                "stations": [
                    {
                        "station_code": 222,
                        "station_name": "Work Station",
                        "au_state": "NSW",
                        "fuel_types": ["DL"],
                    }
                ],
            },
        },
    }


async def test_remove_config_entry_device_removes_matching_nickname(
    hass: HomeAssistant,
) -> None:
    """Removing a nickname device removes only that nickname from config."""
    data = _entry_data()
    expected_work = deepcopy(data["nicknames"]["Work"])
    entry = MockConfigEntry(domain=DOMAIN, data=data)
    entry.add_to_hass(hass)

    device_registry = dr.async_get(hass)
    home_device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "location_Home")},
        name="Home",
    )

    result = await async_remove_config_entry_device(hass, entry, home_device)

    assert result is True
    assert set(entry.data["nicknames"]) == {"Work"}
    assert entry.data["nicknames"]["Work"] == expected_work
    assert entry.data["client_id"] == "test_client_id"
    assert entry.data["client_secret"] == "test_client_secret"


async def test_remove_config_entry_device_allows_last_nickname(
    hass: HomeAssistant,
) -> None:
    """Removing the final nickname leaves the integration configured but empty."""
    data = _entry_data()
    data["nicknames"] = {"Home": data["nicknames"]["Home"]}
    entry = MockConfigEntry(domain=DOMAIN, data=data)
    entry.add_to_hass(hass)

    device_registry = dr.async_get(hass)
    home_device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "location_Home")},
        name="Home",
    )

    result = await async_remove_config_entry_device(hass, entry, home_device)

    assert result is True
    assert entry.data["nicknames"] == {}
    assert entry.data["client_id"] == "test_client_id"
    assert entry.data["client_secret"] == "test_client_secret"


async def test_remove_config_entry_device_rejects_unknown_device(
    hass: HomeAssistant,
) -> None:
    """Reject a device that is not one of this entry's configured nicknames."""
    data = _entry_data()
    original_data = deepcopy(data)
    entry = MockConfigEntry(domain=DOMAIN, data=data)
    entry.add_to_hass(hass)

    device_registry = dr.async_get(hass)
    unknown_device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "not_a_nickname_device")},
        name="Unknown",
    )

    result = await async_remove_config_entry_device(hass, entry, unknown_device)

    assert result is False
    assert entry.data == original_data
