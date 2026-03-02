"""Tests integration of NSWFuelCoordinator and cheapest sensors."""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_component import EntityComponent
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN

import pytest
from nsw_tas_fuel import (
    Price,
    Station,
    StationPrice,
)

from custom_components.nsw_tas_fuel_station.coordinator import NSWFuelCoordinator
from custom_components.nsw_tas_fuel_station.sensor import (
    create_cheapest_fuel_sensors,
)

_LOGGER = logging.getLogger(__name__)

@pytest.fixture
def nickname_home() -> dict:
    """Single NSW nickname."""
    return {
        "Home": {
            "location": {"latitude": 150, "longitude": -35},
            "stations": [],
        }
    }


async def test_cheapest_sensor_updates_on_refresh(
    hass: HomeAssistant,
    mock_api_client,
    nickname_home,
) -> None:
    """Cheapest sensor state updates when coordinator refreshes."""

    # First API response
    async def fake_within_radius_first(*args, **kwargs):
        station = Station(
            ident=None,
            brand="Test",
            code=999,
            name="Station One",
            address="Test",
            latitude=0,
            longitude=0,
            au_state="NSW",
        )

        return [
            StationPrice(
                station=station,
                price=Price(
                    fuel_type="U91",
                    price=170.0,
                    last_updated="2024-01-01T00:00:00Z",
                    price_unit="c/L",
                    station_code=999,
                ),
            )
        ]

    mock_api_client.get_fuel_prices_within_radius.side_effect = fake_within_radius_first

    coordinator = NSWFuelCoordinator(
        hass=hass,
        api=mock_api_client,
        nicknames=nickname_home,
        scan_interval=timedelta(minutes=5),
    )

    # Create cheapest sensor
    sensors = create_cheapest_fuel_sensors(coordinator)

    component = EntityComponent(_LOGGER, SENSOR_DOMAIN, hass)
    await component.async_add_entities(sensors)

    await hass.async_block_till_done()

    sensor = sensors[0]

    # First refresh
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert sensor.native_value == 170.0
    first_state = sensor.native_value

    # Second API response
    async def fake_within_radius_second(*args, **kwargs):
        station = Station(
            ident=None,
            brand="Test",
            code=999,
            name="Station One",
            address="Test",
            latitude=0,
            longitude=0,
            au_state="NSW",
        )

        return [
            StationPrice(
                station=station,
                price=Price(
                    fuel_type="U91",
                    price=160.0,
                    last_updated="2024-01-01T00:00:00Z",
                    price_unit="c/L",
                    station_code=999,
                ),
            )
        ]

    mock_api_client.get_fuel_prices_within_radius.side_effect = (
        fake_within_radius_second
    )

    # Second refresh
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert sensor.native_value == 160.0
    assert sensor.native_value != first_state
