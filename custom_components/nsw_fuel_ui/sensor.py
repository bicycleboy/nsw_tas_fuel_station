"""Sensor platform for NSW Fuel UI."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Optional

from homeassistant.components.sensor import SensorEntity
from homeassistant.const import CURRENCY_CENT
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback
    from homeassistant.exceptions import ConfigEntryNotReady

    from .data import NSWFuelConfigEntry, StationPriceData, Station
    from .coordinator import NSWFuelCoordinator

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
    hass: HomeAssistant,
    entry: NSWFuelConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up all fuel price sensors for configured stations."""
    coordinator: NSWFuelCoordinator = hass.data[DOMAIN][entry.entry_id]

    _LOGGER.debug("Coordinator class: %s", type(coordinator))
    _LOGGER.debug("Has async_wait_for_first_update? %s", hasattr(coordinator, "async_wait_for_first_update"))

    try:
        # Use the new recommended method to do initial data fetch and wait for it
        await coordinator.async_config_entry_first_refresh()
    except ConfigEntryNotReady as err:
        _LOGGER.error("Failed to fetch initial data: %s", err)
        raise

    sensors: list[FuelPriceSensor] = []

    for station_code in coordinator.stations:
        fuel_types = coordinator.get_station_fuel_types(station_code)

        _LOGGER.debug(
            "async_setup_entry fetched fuel types for station %s: %s",
            station_code,
            fuel_types,
        )

        if not fuel_types:
            _LOGGER.warning(
                "No fuel types available for station %s; skipping", station_code
            )
            continue

        for fuel_type in fuel_types:
            sensors.append(
                FuelPriceSensor(
                    coordinator=coordinator,
                    station_code=station_code,
                    fuel_type=fuel_type,
                )
            )

    if sensors:
        async_add_entities(sensors, update_before_add=True)
    else:
        _LOGGER.warning(
            "No sensors created because no fuel types found for any station"
        )


class FuelPriceSensor(CoordinatorEntity["NSWFuelCoordinator"], SensorEntity):
    """Sensor representing fuel price for a station & fuel type."""

    _attr_attribution = "Data provided by NSW Government FuelCheck"

    def __init__(self, coordinator: NSWFuelCoordinator, station_code: int, fuel_type: str) -> None:
        """Initialize sensor."""
        super().__init__(coordinator)

        self._station_code = station_code
        self._fuel_type = fuel_type

        # Use typed Station info from coordinator
        self._station_name = coordinator.get_station_name(station_code)
        state = coordinator.get_station_state(station_code).lower()

        # Unique ID includes state for uniqueness across states
        self._attr_unique_id = f"{state}_{station_code}_{fuel_type}"

        _LOGGER.debug("Created sensor %s with unique_id %s", self.name, self._attr_unique_id)

    @property
    def name(self) -> str:
        """Return human-readable sensor name."""
        return f"{self._station_name} {self._fuel_type}"

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data:
            return self.coordinator.data.prices.get((self._station_code, self._fuel_type))
        return None
