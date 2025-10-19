"""Sensor platform for nsw_fuel_ui."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.sensor import SensorEntity

from .const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .data import NSWFuelConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NSWFuelConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the sensor platform."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([FuelPriceSensor(coordinator)], True)  # noqa: FBT003


class FuelPriceSensor(SensorEntity):
    def __init__(self, coordinator):
        """Initialize the fuel price sensor."""
        self.coordinator = coordinator
        self._attr_name = "Fuel Price"
        self._attr_unique_id = f"fuel_price_{coordinator.station_code}"
        self._attr_icon = "mdi:fuel"

    @property
    def native_value(self):
        data = self.coordinator.data.get("prices")
        return data["price"] if data else None

    @property
    def native_unit_of_measurement(self):
        return "c/L"

    async def async_update(self):
        await self.coordinator.async_request_refresh()
