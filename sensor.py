"""Sensor platform for NSW Fuel UI."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity


from .const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .data import NSWFuelConfigEntry, NSWFuelCoordinator

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
    hass: HomeAssistant,
    entry: NSWFuelConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up fuel sensor from a config entry."""
    coordinator: NSWFuelCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([FuelPriceSensor(coordinator)], update_before_add=True)


class FuelPriceSensor(CoordinatorEntity, SensorEntity):
    """Representation of the current fuel price."""

    def __init__(self, coordinator: NSWFuelCoordinator) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._attr_name = f"Fuel Price ({coordinator.station_code})"
        self._attr_unique_id = f"fuel_price_{coordinator.station_code}"
        self._attr_icon = "mdi:gas-station"

    @property
    def native_value(self) -> float | None:
        """Return the latest price from the coordinator."""
        data: dict[str, Any] | None = self.coordinator.data
        if not data:
            return None



        prices = data.get("prices")
        _LOGGER.debug("Fuel prices data: %s", prices)
        #if isinstance(prices, dict):
        #    return prices.get("price")
        if isinstance(prices, dict) and prices:
            # Just take the first entry for now
            prices_list = prices["prices"]
            return prices_list[0].get("price")


        return None

    @property
    def native_unit_of_measurement(self) -> str:
        """Unit of the fuel price."""
        return "c/L"
