"""Tests for NSW Fuel Check integration migration."""

from __future__ import annotations

from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

from custom_components.nsw_tas_fuel_station import async_migrate_entry
from custom_components.nsw_tas_fuel_station.const import DOMAIN


async def test_migrate_favorite_sensor_unique_ids(hass: HomeAssistant) -> None:
    """Migrate only legacy favorite IDs while preserving entity IDs."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=1,
        data={
            "client_id": "test_client_id",
            "client_secret": "test_client_secret",
            "nicknames": {
                "Home": {
                    "stations": [
                        {
                            "station_code": 111,
                            "au_state": "NSW",
                            "station_name": "Shared Station",
                            "fuel_types": ["U91"],
                        }
                    ]
                },
                "Work": {
                    "stations": [
                        {
                            "station_code": 111,
                            "au_state": "NSW",
                            "station_name": "Shared Station",
                            "fuel_types": ["U91"],
                        }
                    ]
                },
            },
        },
    )
    entry.add_to_hass(hass)

    device_registry = dr.async_get(hass)
    home_device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "location_Home")},
        name="Home",
    )
    work_device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "location_Work")},
        name="Work",
    )

    entity_registry = er.async_get(hass)

    legacy_favorite = entity_registry.async_get_or_create(
        SENSOR_DOMAIN,
        DOMAIN,
        f"{DOMAIN}_111_NSW_U91",
        config_entry=entry,
        device_id=home_device.id,
        suggested_object_id="home_shared_station_u91",
    )
    favorite_entity_id = legacy_favorite.entity_id

    cheapest = entity_registry.async_get_or_create(
        SENSOR_DOMAIN,
        DOMAIN,
        f"{DOMAIN}_cheapest_Home_1",
        config_entry=entry,
        device_id=home_device.id,
        suggested_object_id="cheapest_home_1",
    )
    cheapest_entity_id = cheapest.entity_id

    unrelated = entity_registry.async_get_or_create(
        SENSOR_DOMAIN,
        DOMAIN,
        f"{DOMAIN}_diagnostic_example",
        config_entry=entry,
        device_id=work_device.id,
        suggested_object_id="diagnostic_example",
    )
    unrelated_entity_id = unrelated.entity_id

    assert await async_migrate_entry(hass, entry)

    assert entry.version == 2

    migrated_favorite = entity_registry.async_get(favorite_entity_id)
    assert migrated_favorite is not None
    assert migrated_favorite.entity_id == favorite_entity_id
    assert migrated_favorite.unique_id == f"{DOMAIN}_Home_111_NSW_U91"

    unchanged_cheapest = entity_registry.async_get(cheapest_entity_id)
    assert unchanged_cheapest is not None
    assert unchanged_cheapest.unique_id == f"{DOMAIN}_cheapest_Home_1"

    unchanged_unrelated = entity_registry.async_get(unrelated_entity_id)
    assert unchanged_unrelated is not None
    assert unchanged_unrelated.unique_id == f"{DOMAIN}_diagnostic_example"


async def test_migrate_entry_version_two_is_noop(hass: HomeAssistant) -> None:
    """Version 2 entries do not run the legacy unique-ID migration again."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        data={"client_id": "test", "client_secret": "test", "nicknames": {}},
    )
    entry.add_to_hass(hass)

    assert await async_migrate_entry(hass, entry)
    assert entry.version == 2
