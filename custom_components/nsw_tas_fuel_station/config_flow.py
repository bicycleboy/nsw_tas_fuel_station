"""Config flow for NSW Fuel Check integration."""

from __future__ import annotations

import copy
import logging
import math
import re
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, cast

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_CLIENT_ID, CONF_CLIENT_SECRET, UnitOfLength
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    BooleanSelectorConfig,
    LocationSelector,
    LocationSelectorConfig,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from homeassistant.util.unit_conversion import DistanceConverter
from nsw_tas_fuel import (
    NSWFuelApiClient,
    NSWFuelApiClientAuthError,
    NSWFuelApiClientError,
)

from .const import (
    ALL_FUEL_TYPES,
    CONF_AU_STATE,
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
    CONF_STATION_NAME,
    DEFAULT_EXCLUDE_STRING,
    DEFAULT_FUEL_TYPE,
    DEFAULT_FUEL_TYPE_NON_E10,
    DEFAULT_NICKNAME,
    DEFAULT_RADIUS_KM,
    DOMAIN,
    LAT_CAMERON_CORNER_BOUND,
    LAT_SE_BOUND,
    LAT_TAS_N_BOUND,
    LON_CAMERON_CORNER_BOUND,
    LON_SE_BOUND,
    STATION_LIST_LIMIT,
)
from .coordinator import state_default_fuel

if TYPE_CHECKING:
    from nsw_tas_fuel.client import StationPrice

_LOGGER = logging.getLogger(__name__)


class NSWFuelConfigFlow(ConfigFlow, domain=DOMAIN):
    """Config flow for NSW Fuel Check Integration.

    Config flow uses nicknames as logical grouping (ie "home", "work", "trip to work")
    to support "cheapest near ..." sensors but also to give user more options in UI
    """

    VERSION = 1

    def __init__(self) -> None:
        """Init Config Flow."""
        self._flow_data: dict[str, Any] = {}
        self._last_form: dict[str, Any] = {}
        self._nearby_station_prices: list[StationPrice] = []
        self._station_lookup: dict[int, dict[str, Any]] = {}
        self.api: NSWFuelApiClient | None = None
        self._config_entry: config_entries.ConfigEntry | None = None
        self._managed_nickname: str | None = None
        self._managed_station_code: int | None = None
        self._managed_available_fuels: list[str] | None = None
        self._add_to_existing_nickname = False

    def is_matching(self, other_flow: Self) -> bool:
        """Return True if other_flow is matching this flow.

        Not applicable — this integration has no device-discovery flows.
        """
        return False

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 1: no options, just verify credentials and a list of stations near home zone.

        Validates:
        - Account uniqueness (client_id)
        - HA Home location within supported AU states
        - API connectivity

        """

        errors: dict[str, str] = {}

        if user_input is None:
            return self.async_show_form(
                step_id="user",
                data_schema=self._build_user_schema(),
                errors=errors,
            )

        self._last_form = user_input
        client_id = user_input[CONF_CLIENT_ID]
        client_secret = user_input[CONF_CLIENT_SECRET]
        self._flow_data.update(
            {
                CONF_CLIENT_ID: client_id,
                CONF_CLIENT_SECRET: client_secret,
            }
        )

        await self.async_set_unique_id(client_id)
        self._abort_if_unique_id_configured()

        nickname = DEFAULT_NICKNAME

        location = {
            CONF_LATITUDE: getattr(self.hass.config, CONF_LATITUDE, None),
            CONF_LONGITUDE: getattr(self.hass.config, CONF_LONGITUDE, None),
        }

        # Create the API client before validating the location so we can error to advanced path
        session = async_get_clientsession(self.hass)
        self.api = NSWFuelApiClient(
            session=session,
            client_id=client_id,
            client_secret=client_secret,
        )

        try:
            lat, lon, au_state = _validate_location(location)
        except ValueError as err:
            errors["base"] = str(err)
            _LOGGER.warning("Invalid HA Home location: %s", err)

            return self.async_show_form(
                step_id="advanced_options",
                data_schema=self._build_advanced_options_schema(
                    self._last_form or self._flow_data
                ),
                errors=errors,
            )

        fuel_type = state_default_fuel(au_state)

        # First network API call: validates credentials + gathers nearby stations
        errors = await self._get_station_list(
            lat,
            lon,
            DEFAULT_RADIUS_KM,
            fuel_type,
        )

        if errors:
            return self.async_show_form(
                step_id="user",
                data_schema=self._build_user_schema(self._last_form),
                errors=errors,
            )

        self._flow_data.update(
            {
                CONF_NICKNAME: nickname,
                CONF_AU_STATE: au_state,
                CONF_FUEL_TYPE: fuel_type,
                CONF_LOCATION: {
                    CONF_LATITUDE: lat,
                    CONF_LONGITUDE: lon,
                },
            }
        )

        if not self._nearby_station_prices:
            errors["base"] = "no_stations"
            return self.async_show_form(
                step_id="advanced_options",
                data_schema=self._build_advanced_options_schema(self._flow_data),
                errors=errors,
            )

        return await self.async_step_station_select()

    async def async_step_station_select(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 2: Present user with a list of nearby stations to select from, process selection."""

        errors: dict[str, str] = {}

        if user_input is None:
            return self.async_show_form(
                step_id="station_select",
                data_schema=self._build_station_schema(),
                errors=errors,
            )

        nickname = self._flow_data.get(CONF_NICKNAME, DEFAULT_NICKNAME)

        selected_stations = [int(x) for x in user_input.get(CONF_SELECTED_STATIONS, [])]

        if not selected_stations:
            errors["base"] = "no_stations"
            return self.async_show_form(
                step_id="station_select",
                data_schema=self._build_station_schema(),
                errors=errors,
            )

        self._flow_data[CONF_SELECTED_STATIONS] = selected_stations

        stations_config_entry = [
            {
                CONF_STATION_CODE: code,
                CONF_AU_STATE: self._station_lookup[code][CONF_AU_STATE],
                CONF_STATION_NAME: self._station_lookup[code][CONF_STATION_NAME],
                CONF_STATION_FUEL_TYPES: self._station_lookup[code][
                    CONF_STATION_FUEL_TYPES
                ],
            }
            for code in selected_stations
        ]

        # Reconfigure flow: add nickname, add stations to a nickname, add fuel to stations
        if self._config_entry is not None:
            existing_config_entry = self._config_entry.data
            existing_nicknames = existing_config_entry.get("nicknames", {})

            if self._add_to_existing_nickname:
                if nickname not in existing_nicknames:
                    return self.async_abort(reason="unknown_entry")

                # Add only the selected station/fuels. Searching from a different
                # point or fuel must not silently alter this location's saved
                # cheapest-fuel settings, radius, exclusion, or coordinates.
                new_config_entry = _add_stations_to_nickname(
                    existing_config_entry,
                    nickname,
                    None,
                    stations_config_entry,
                )
                new_config_entry = _add_fuel_to_stations(
                    new_config_entry, nickname, stations_config_entry
                )
                self.hass.config_entries.async_update_entry(
                    self._config_entry, data=new_config_entry
                )
                return self.async_abort(reason="stations_added")

            if nickname not in existing_nicknames:
                new_config_entry = _create_nickname_with_stations(
                    existing_config_entry,
                    nickname,
                    self._flow_data.get(CONF_LOCATION),
                    stations_config_entry,
                    self._flow_data.get(CONF_RADIUS_KM, DEFAULT_RADIUS_KM),
                    self._flow_data.get(CONF_FUEL_TYPE),
                    self._flow_data.get(CONF_EXCLUDE_STRING, DEFAULT_EXCLUDE_STRING),
                )
                self.hass.config_entries.async_update_entry(
                    self._config_entry, data=new_config_entry
                )
                return self.async_abort(reason="nickname_created")

            new_config_entry = _add_stations_to_nickname(
                existing_config_entry,
                nickname,
                self._flow_data.get(CONF_LOCATION),
                stations_config_entry,
                self._flow_data.get(CONF_RADIUS_KM, DEFAULT_RADIUS_KM),
                self._flow_data.get(CONF_FUEL_TYPE),
                self._flow_data.get(CONF_EXCLUDE_STRING, DEFAULT_EXCLUDE_STRING),
            )
            new_config_entry = _add_fuel_to_stations(
                new_config_entry, nickname, stations_config_entry
            )

            self.hass.config_entries.async_update_entry(
                self._config_entry, data=new_config_entry
            )
            return self.async_abort(reason="reconfigured")

        # Initial flow, create config entry with default options
        return await self._create_new_config_entry(nickname, selected_stations)

    async def async_step_reconfigure(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Reconfigure an existing config entry.

        Supports adding a nickname/location, adding a station to a nickname,
        adding a fuel to existing stations.
        """

        self._config_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )

        if self._config_entry is None:
            return self.async_abort(reason="unknown_entry")

        await self.async_set_unique_id(self._config_entry.data[CONF_CLIENT_ID])
        self._abort_if_unique_id_mismatch()

        self._flow_data = dict(self._config_entry.data)
        existing_nicknames = self._flow_data.get("nicknames", {})

        if CONF_NICKNAME not in self._flow_data and existing_nicknames:
            self._flow_data[CONF_NICKNAME] = (
                DEFAULT_NICKNAME
                if DEFAULT_NICKNAME in existing_nicknames
                else next(iter(existing_nicknames))
            )

        if self.api is None:
            self.api = NSWFuelApiClient(
                session=async_get_clientsession(self.hass),
                client_id=self._config_entry.data[CONF_CLIENT_ID],
                client_secret=self._config_entry.data[CONF_CLIENT_SECRET],
            )

        if user_input is not None:
            return await self.async_step_advanced_options(user_input)

        return self.async_show_menu(
            step_id="reconfigure",
            menu_options=[
                "advanced_options",
                "add_station_existing",
                "edit_location",
                "manage_stations",
                "delete_location",
            ],
        )

    async def async_step_add_station_existing(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose an existing nickname/location to add a station to."""
        if self._config_entry is None:
            return self.async_abort(reason="unknown_entry")

        nicknames = self._config_entry.data.get("nicknames", {})
        if not nicknames:
            return self.async_abort(reason="no_configured_locations")

        if user_input is not None:
            self._managed_nickname = cast(str, user_input[CONF_NICKNAME])
            self._add_to_existing_nickname = True
            return await self.async_step_add_station_search()

        return self.async_show_form(
            step_id="add_station_existing",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_NICKNAME): SelectSelector(
                        SelectSelectorConfig(
                            options=self._nickname_options(nicknames),
                            multiple=False,
                            sort=False,
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
        )

    async def async_step_add_station_search(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Search for a station to add to an existing nickname/location."""
        if self._config_entry is None or self._managed_nickname is None:
            return self.async_abort(reason="unknown_entry")

        nickname_data = self._config_entry.data.get("nicknames", {}).get(
            self._managed_nickname
        )
        if nickname_data is None:
            return self.async_abort(reason="unknown_entry")

        saved_location = nickname_data.get(CONF_LOCATION) or {}
        saved_radius_km = nickname_data.get(CONF_RADIUS_KM, DEFAULT_RADIUS_KM)
        form_location = {
            **saved_location,
            CONF_RADIUS_M: DistanceConverter.convert(
                saved_radius_km,
                UnitOfLength.KILOMETERS,
                UnitOfLength.METERS,
            ),
        }

        if user_input is not None:
            errors: dict[str, str] = {}
            location = user_input.get(CONF_LOCATION)
            try:
                lat, lon, au_state = _validate_location(location)
            except ValueError as err:
                errors["base"] = str(err)
            else:
                radius_meters = location.get(
                    CONF_RADIUS_M, DEFAULT_RADIUS_KM * 1000
                )
                radius_km = max(
                    1,
                    math.ceil(
                        DistanceConverter.convert(
                            radius_meters,
                            UnitOfLength.METERS,
                            UnitOfLength.KILOMETERS,
                        )
                    ),
                )
                fuel_type = cast(str, user_input[CONF_FUEL_TYPE])
                _default_fuel, available_fuels = _get_state_defaults(
                    {CONF_LATITUDE: lat, CONF_LONGITUDE: lon}
                )
                valid_fuels = {code for code, _label in available_fuels}
                if fuel_type not in valid_fuels:
                    errors["base"] = "invalid_fuel_for_location"

            if not errors:
                self._flow_data.update(
                    {
                        CONF_NICKNAME: self._managed_nickname,
                        CONF_LOCATION: {
                            CONF_LATITUDE: lat,
                            CONF_LONGITUDE: lon,
                        },
                        CONF_AU_STATE: au_state,
                        CONF_FUEL_TYPE: fuel_type,
                        CONF_RADIUS_KM: radius_km,
                    }
                )
                errors = await self._get_station_list(
                    lat, lon, radius_km, fuel_type
                )

            if not errors and not self._nearby_station_prices:
                errors["base"] = "no_stations"

            if not errors:
                return await self.async_step_station_select()

            form_location = user_input.get(CONF_LOCATION, form_location)
        else:
            errors = {}

        suggested_fuel, fuel_types = _get_state_defaults(form_location)
        selected_fuel = (
            user_input.get(CONF_FUEL_TYPE)
            if user_input is not None
            else nickname_data.get(CONF_CHEAPEST_FUEL_TYPE, suggested_fuel)
        )
        valid_codes = {code for code, _label in fuel_types}
        if selected_fuel not in valid_codes:
            selected_fuel = suggested_fuel

        return self.async_show_form(
            step_id="add_station_search",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_LOCATION,
                        default=form_location,
                    ): LocationSelector(LocationSelectorConfig(radius=True)),
                    vol.Required(
                        CONF_FUEL_TYPE,
                        default=selected_fuel,
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                SelectOptionDict(value=code, label=label)
                                for code, label in fuel_types
                            ],
                            multiple=False,
                            sort=False,
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_edit_location(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose an existing nickname/location to edit."""
        if self._config_entry is None:
            return self.async_abort(reason="unknown_entry")

        nicknames = self._config_entry.data.get("nicknames", {})
        if not nicknames:
            return self.async_abort(reason="no_configured_locations")

        if user_input is not None:
            self._managed_nickname = cast(str, user_input[CONF_NICKNAME])
            return await self.async_step_edit_location_settings()

        return self.async_show_form(
            step_id="edit_location",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_NICKNAME): SelectSelector(
                        SelectSelectorConfig(
                            options=self._nickname_options(nicknames),
                            multiple=False,
                            sort=False,
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
        )

    async def async_step_edit_location_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit settings for an existing nickname/location without selecting stations."""
        if self._config_entry is None or self._managed_nickname is None:
            return self.async_abort(reason="unknown_entry")

        nickname_data = self._config_entry.data.get("nicknames", {}).get(
            self._managed_nickname
        )
        if nickname_data is None:
            return self.async_abort(reason="unknown_entry")

        saved_location = nickname_data.get(CONF_LOCATION) or {}
        saved_radius_km = nickname_data.get(CONF_RADIUS_KM, DEFAULT_RADIUS_KM)
        form_location = {
            **saved_location,
            CONF_RADIUS_M: DistanceConverter.convert(
                saved_radius_km,
                UnitOfLength.KILOMETERS,
                UnitOfLength.METERS,
            ),
        }

        if user_input is not None:
            errors: dict[str, str] = {}
            location = user_input.get(CONF_LOCATION)
            try:
                lat, lon, _au_state = _validate_location(location)
            except ValueError as err:
                errors["base"] = str(err)
            else:
                radius_meters = location.get(
                    CONF_RADIUS_M, DEFAULT_RADIUS_KM * 1000
                )
                radius_km = max(
                    1,
                    math.ceil(
                        DistanceConverter.convert(
                            radius_meters,
                            UnitOfLength.METERS,
                            UnitOfLength.KILOMETERS,
                        )
                    ),
                )
                selected_fuel = cast(str, user_input[CONF_FUEL_TYPE])
                _default_fuel, available_fuels = _get_state_defaults(
                    {CONF_LATITUDE: lat, CONF_LONGITUDE: lon}
                )
                valid_fuels = {code for code, _label in available_fuels}
                if selected_fuel not in valid_fuels:
                    errors["base"] = "invalid_fuel_for_location"

                if not errors:
                    if self.api is None:
                        errors["base"] = "connection"
                    else:
                        try:
                            nearby = await self.api.get_fuel_prices_within_radius(
                                latitude=lat,
                                longitude=lon,
                                radius=radius_km,
                                fuel_type=selected_fuel,
                            )
                        except NSWFuelApiClientAuthError:
                            errors["base"] = "auth"
                        except NSWFuelApiClientError:
                            errors["base"] = "no_prices_for_location"
                        else:
                            exclude_string = user_input.get(
                                CONF_EXCLUDE_STRING, DEFAULT_EXCLUDE_STRING
                            )
                            matching = [
                                station_price
                                for station_price in nearby
                                if not exclude_string
                                or exclude_string.lower()
                                not in station_price.station.name.lower()
                            ]
                            if not matching:
                                errors["base"] = "no_prices_for_location"

            if not errors:
                new_data = copy.deepcopy(dict(self._config_entry.data))
                block = new_data["nicknames"][self._managed_nickname]
                block[CONF_LOCATION] = {
                    CONF_LATITUDE: lat,
                    CONF_LONGITUDE: lon,
                }
                block[CONF_RADIUS_KM] = radius_km
                block[CONF_CHEAPEST_FUEL_TYPE] = selected_fuel
                block[CONF_EXCLUDE_STRING] = user_input.get(
                    CONF_EXCLUDE_STRING, DEFAULT_EXCLUDE_STRING
                )
                self.hass.config_entries.async_update_entry(
                    self._config_entry, data=new_data
                )
                return self.async_abort(reason="location_updated")

            form_location = user_input.get(CONF_LOCATION, form_location)
        else:
            errors = {}

        suggested_fuel, fuel_types = _get_state_defaults(form_location)
        selected_fuel = (
            user_input.get(CONF_FUEL_TYPE)
            if user_input is not None
            else nickname_data.get(CONF_CHEAPEST_FUEL_TYPE, suggested_fuel)
        )
        exclude_string = (
            user_input.get(CONF_EXCLUDE_STRING, DEFAULT_EXCLUDE_STRING)
            if user_input is not None
            else nickname_data.get(CONF_EXCLUDE_STRING, DEFAULT_EXCLUDE_STRING)
        )

        return self.async_show_form(
            step_id="edit_location_settings",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_LOCATION,
                        default=form_location,
                    ): LocationSelector(LocationSelectorConfig(radius=True)),
                    vol.Required(
                        CONF_FUEL_TYPE,
                        default=selected_fuel,
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                SelectOptionDict(value=code, label=label)
                                for code, label in fuel_types
                            ],
                            multiple=False,
                            sort=False,
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    ),
                    vol.Optional(
                        CONF_EXCLUDE_STRING,
                        description={"suggested_value": exclude_string},
                    ): TextSelector(),
                }
            ),
            errors=errors,
        )

    async def async_step_delete_location(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose an existing nickname/location to delete."""
        if self._config_entry is None:
            return self.async_abort(reason="unknown_entry")

        nicknames = self._config_entry.data.get("nicknames", {})
        if not nicknames:
            return self.async_abort(reason="no_configured_locations")

        if user_input is not None:
            self._managed_nickname = cast(str, user_input[CONF_NICKNAME])
            return await self.async_step_confirm_delete_location()

        return self.async_show_form(
            step_id="delete_location",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_NICKNAME): SelectSelector(
                        SelectSelectorConfig(
                            options=self._nickname_options(nicknames),
                            multiple=False,
                            sort=False,
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
        )

    async def async_step_confirm_delete_location(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm deletion of an entire nickname/location."""
        if self._config_entry is None or self._managed_nickname is None:
            return self.async_abort(reason="unknown_entry")

        if self._managed_nickname not in self._config_entry.data.get("nicknames", {}):
            return self.async_abort(reason="unknown_entry")

        if user_input is None:
            return self.async_show_form(
                step_id="confirm_delete_location",
                data_schema=vol.Schema({}),
                description_placeholders={
                    "location": self._nickname_label(self._managed_nickname)
                },
            )

        new_data = copy.deepcopy(dict(self._config_entry.data))
        new_data.get("nicknames", {}).pop(self._managed_nickname, None)

        _remove_nickname_device_and_entities(
            self.hass,
            self._config_entry,
            self._managed_nickname,
        )
        self.hass.config_entries.async_update_entry(
            self._config_entry, data=new_data
        )
        return self.async_abort(reason="location_removed")

    async def async_step_manage_stations(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose an existing nickname whose stations will be managed."""
        if self._config_entry is None:
            return self.async_abort(reason="unknown_entry")

        nicknames = self._config_entry.data.get("nicknames", {})
        if not nicknames:
            return self.async_abort(reason="no_configured_stations")

        if user_input is not None:
            self._managed_nickname = cast(str, user_input[CONF_NICKNAME])
            return await self.async_step_manage_station()

        options = self._nickname_options(nicknames)
        return self.async_show_form(
            step_id="manage_stations",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_NICKNAME): SelectSelector(
                        SelectSelectorConfig(
                            options=options,
                            multiple=False,
                            sort=False,
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
        )

    async def async_step_manage_station(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose a configured station to edit or remove."""
        if self._config_entry is None or self._managed_nickname is None:
            return self.async_abort(reason="unknown_entry")

        nickname_data = self._config_entry.data.get("nicknames", {}).get(
            self._managed_nickname, {}
        )
        stations = nickname_data.get("stations", [])
        if not stations:
            return self.async_abort(reason="no_configured_stations")

        if user_input is not None:
            self._managed_station_code = int(user_input[CONF_STATION_CODE])
            self._managed_available_fuels = None
            return await self.async_step_edit_station()

        options = [
            SelectOptionDict(
                value=str(station[CONF_STATION_CODE]),
                label=f"{station[CONF_STATION_NAME]} ({station[CONF_STATION_CODE]})",
            )
            for station in stations
        ]
        return self.async_show_form(
            step_id="manage_station",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_STATION_CODE): SelectSelector(
                        SelectSelectorConfig(
                            options=options,
                            multiple=False,
                            sort=False,
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
        )

    async def async_step_edit_station(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit configured fuels for a station or remove the station."""
        if (
            self._config_entry is None
            or self._managed_nickname is None
            or self._managed_station_code is None
        ):
            return self.async_abort(reason="unknown_entry")

        nickname_data = self._config_entry.data.get("nicknames", {}).get(
            self._managed_nickname, {}
        )
        station = next(
            (
                item
                for item in nickname_data.get("stations", [])
                if item[CONF_STATION_CODE] == self._managed_station_code
            ),
            None,
        )
        if station is None:
            return self.async_abort(reason="station_not_found")

        configured_fuels = list(station.get(CONF_STATION_FUEL_TYPES, []))

        if self._managed_available_fuels is None:
            if self.api is None:
                return self.async_abort(reason="unknown_entry")

            try:
                prices = await self.api.get_fuel_prices_for_station(
                    str(self._managed_station_code),
                    station[CONF_AU_STATE],
                )
            except NSWFuelApiClientAuthError:
                return self.async_abort(reason="auth")
            except NSWFuelApiClientError:
                return self.async_abort(reason="connection")

            self._managed_available_fuels = sorted(
                {
                    *configured_fuels,
                    *(price.fuel_type for price in prices),
                }
            )

        fuel_options = [
            SelectOptionDict(value=code, label=ALL_FUEL_TYPES.get(code, code))
            for code in self._managed_available_fuels
        ]

        if user_input is not None:
            remove_station = bool(user_input.get("remove_station", False))
            selected_fuels = list(user_input.get(CONF_STATION_FUEL_TYPES, []))

            if not remove_station and not selected_fuels:
                return self.async_show_form(
                    step_id="edit_station",
                    data_schema=self._build_edit_station_schema(
                        fuel_options, configured_fuels
                    ),
                    errors={"base": "select_fuel_or_remove_station"},
                )

            if (
                remove_station
                and len(nickname_data.get("stations", [])) == 1
            ):
                return await self.async_step_confirm_remove_empty_location()

            removed_fuels = (
                configured_fuels
                if remove_station
                else [
                    fuel
                    for fuel in configured_fuels
                    if fuel not in selected_fuels
                ]
            )
            new_data = _update_configured_station(
                self._config_entry.data,
                self._managed_nickname,
                self._managed_station_code,
                selected_fuels,
                remove_station=remove_station,
            )
            _remove_station_entities(
                self.hass,
                self._config_entry,
                self._managed_nickname,
                self._managed_station_code,
                station[CONF_AU_STATE],
                removed_fuels,
            )
            self.hass.config_entries.async_update_entry(
                self._config_entry, data=new_data
            )

            # Keep the management flow open so multiple stations can be
            # maintained in one session. The config-entry update listener
            # reloads the integration; no extra explicit reload is needed.
            self._managed_station_code = None
            self._managed_available_fuels = None
            return await self.async_step_manage_station()

        return self.async_show_form(
            step_id="edit_station",
            data_schema=self._build_edit_station_schema(
                fuel_options, configured_fuels
            ),
        )

    async def async_step_confirm_remove_empty_location(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm removal of a location when its final station is removed."""
        if self._config_entry is None or self._managed_nickname is None:
            return self.async_abort(reason="unknown_entry")

        if user_input is None:
            return self.async_show_form(
                step_id="confirm_remove_empty_location",
                data_schema=vol.Schema({}),
            )

        new_data = copy.deepcopy(dict(self._config_entry.data))
        new_data.get("nicknames", {}).pop(self._managed_nickname, None)

        _remove_nickname_device_and_entities(
            self.hass,
            self._config_entry,
            self._managed_nickname,
        )
        self.hass.config_entries.async_update_entry(
            self._config_entry, data=new_data
        )
        return self.async_abort(reason="location_removed")

    def _nickname_label(self, nickname: str) -> str:
        """Return the HA device display name for a stored nickname when available."""
        if self._config_entry is None:
            return nickname

        device_registry = dr.async_get(self.hass)
        device_id = dr.async_get_device_id_by_identifier(
            self.hass,
            (DOMAIN, f"location_{nickname}"),
            config_entry_id=self._config_entry.entry_id,
        )
        device = device_registry.async_get(device_id) if device_id else None
        if device is not None and device.name_by_user:
            return device.name_by_user
        if device is not None and device.name:
            return device.name
        return nickname

    def _nickname_options(
        self, nicknames: Mapping[str, Any]
    ) -> list[SelectOptionDict]:
        """Return nickname options labelled with the HA device display name."""
        return [
            SelectOptionDict(value=nickname, label=self._nickname_label(nickname))
            for nickname in nicknames
        ]

    @staticmethod
    def _build_edit_station_schema(
        fuel_options: list[SelectOptionDict],
        configured_fuels: list[str],
    ) -> vol.Schema:
        """Build the station edit form."""
        return vol.Schema(
            {
                vol.Required(
                    CONF_STATION_FUEL_TYPES,
                    default=configured_fuels,
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=fuel_options,
                        multiple=True,
                        sort=False,
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Optional("remove_station", default=False): BooleanSelector(
                    BooleanSelectorConfig()
                ),
            }
        )

    async def _create_new_config_entry(
        self, nickname: str, selected_stations: list[int]
    ) -> ConfigFlowResult:
        """Create a config entry for NSW Fuel Check integration.

        Store metadata from API to save coordinator additional API calls.
        Called from default path, but also advanced if home location invalid.
        """

        entry = {
            CONF_CLIENT_ID: self._flow_data[CONF_CLIENT_ID],
            CONF_CLIENT_SECRET: self._flow_data[CONF_CLIENT_SECRET],
            "nicknames": {
                nickname: {
                    CONF_LOCATION: self._flow_data.get(CONF_LOCATION),
                    CONF_RADIUS_KM: self._flow_data.get(
                        CONF_RADIUS_KM, DEFAULT_RADIUS_KM
                    ),
                    CONF_CHEAPEST_FUEL_TYPE: self._flow_data.get(
                        CONF_FUEL_TYPE,
                        state_default_fuel(self._flow_data.get(CONF_AU_STATE)),
                    ),
                    CONF_EXCLUDE_STRING: self._flow_data.get(
                        CONF_EXCLUDE_STRING, DEFAULT_EXCLUDE_STRING
                    ),
                    "stations": [
                        {
                            CONF_STATION_CODE: code,
                            CONF_AU_STATE: self._station_lookup[code][CONF_AU_STATE],
                            CONF_STATION_NAME: self._station_lookup[code][
                                CONF_STATION_NAME
                            ],
                            CONF_STATION_FUEL_TYPES: self._station_lookup[code][
                                CONF_STATION_FUEL_TYPES
                            ],
                        }
                        for code in selected_stations
                    ],
                }
            },
        }

        return self.async_create_entry(
            title="NSW Fuel Check",
            data=entry,
        )

    def _build_user_schema(
        self, user_input: dict[str, Any] | None = None
    ) -> vol.Schema:
        """Build config flow UI schema to get API credentials."""

        schema = vol.Schema(
            {
                vol.Required(CONF_CLIENT_ID): TextSelector(),
                vol.Required(CONF_CLIENT_SECRET): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.PASSWORD)
                ),
            }
        )

        config_entry: dict[str, Any] = {}

        if user_input:
            config_entry = user_input
        else:
            entries = self._async_current_entries()
            if entries:
                config_entry = dict(entries[0].data)

        return self.add_suggested_values_to_schema(schema, config_entry)

    def _build_station_schema(
        self,
    ) -> vol.Schema:
        """Build config flow UI schema for the station selection list/dropdown."""

        selected = self._flow_data.get(CONF_SELECTED_STATIONS, [])

        options: list[SelectOptionDict] = [
            cast(
                SelectOptionDict,
                {
                    "value": str(sp.station.code),
                    "label": _format_station_option(sp),
                },
            )
            for sp in self._nearby_station_prices
        ]

        select_selector = SelectSelector(
            SelectSelectorConfig(
                options=options,
                mode=SelectSelectorMode.DROPDOWN,
                multiple=True,
                sort=False,
            )
        )

        return vol.Schema(
            {
                vol.Required(
                    CONF_SELECTED_STATIONS,
                    default=selected,
                ): select_selector,
            }
        )

    async def async_step_advanced_options(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show reconfigure/advanced options step.

        Choose non-default or additional nickname,
        change location, select non-default fuel,
        exclude stations from cheapest.
        """

        errors: dict[str, str] = {}

        if user_input is None:
            return self.async_show_form(
                step_id="advanced_options",
                data_schema=self._build_advanced_options_schema(self._flow_data),
                errors=errors,
            )

        # Nickname used in sensor names so validate
        nickname = user_input.get(CONF_NICKNAME)
        if not nickname or not re.match(r"^[A-Za-z0-9_ -]+$", nickname):
            errors["nickname"] = "invalid_nickname"
        elif (
            self._config_entry is not None
            and nickname in self._config_entry.data.get("nicknames", {})
        ):
            errors["nickname"] = "nickname_already_exists"

        try:
            lat, lon, au_state = _validate_location(user_input.get(CONF_LOCATION))
        except ValueError as err:
            errors["base"] = str(err)

        # Schema enforces fuel type.
        fuel_type = cast(str, user_input[CONF_FUEL_TYPE])

        radius_meters = user_input[CONF_LOCATION].get(
            CONF_RADIUS_M, DEFAULT_RADIUS_KM * 1000
        )
        radius_km = DistanceConverter.convert(
            radius_meters,
            UnitOfLength.METERS,
            UnitOfLength.KILOMETERS,
        )
        radius_km = max(1, math.ceil(radius_km))

        if errors:
            return self.async_show_form(
                step_id="advanced_options",
                data_schema=self._build_advanced_options_schema(user_input),
                errors=errors,
            )

        self._flow_data.update(
            {
                CONF_NICKNAME: nickname,
                CONF_LOCATION: {"latitude": lat, "longitude": lon},
                CONF_AU_STATE: au_state,
                CONF_FUEL_TYPE: fuel_type,
                CONF_RADIUS_KM: radius_km,
                CONF_EXCLUDE_STRING: user_input.get(
                    CONF_EXCLUDE_STRING, DEFAULT_EXCLUDE_STRING
                ),
            }
        )

        errors = await self._get_station_list(lat, lon, radius_km, fuel_type)
        if errors:
            return self.async_show_form(
                step_id="advanced_options",
                data_schema=self._build_advanced_options_schema(user_input),
                errors=errors,
            )

        if not self._nearby_station_prices:
            errors["base"] = "no_stations"
            return self.async_show_form(
                step_id="advanced_options",
                data_schema=self._build_advanced_options_schema(user_input),
                errors=errors,
            )

        return await self.async_step_station_select()

    def _build_advanced_options_schema(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> vol.Schema:
        """Build UI schema for advanced options: nickname, location, fuel type."""

        user = user_input or {}
        existing_nickname: dict[str, Any] = {}

        # Set nickname keeping any invalid nicknames for user correction
        nickname = user.get(
            CONF_NICKNAME,
            self._flow_data.get(CONF_NICKNAME, DEFAULT_NICKNAME),
        )

        if isinstance(nickname, str):
            existing_nickname = self._flow_data.get("nicknames", {}).get(nickname, {})

        default_location = {
            "latitude": getattr(self.hass.config, "latitude", None),
            "longitude": getattr(self.hass.config, "longitude", None),
        }
        location_source = (
            user.get(CONF_LOCATION)
            or existing_nickname.get(CONF_LOCATION)
            or self._flow_data.get(CONF_LOCATION)
            or {}
        )
        suggested_location = {
            **default_location,
            **(location_source if isinstance(location_source, dict) else {}),
        }

        suggested_fuel, fuel_types = _get_state_defaults(suggested_location)
        fuel_options = [
            SelectOptionDict(
                value=code,
                label=name,
            )
            for code, name in fuel_types
        ]
        valid_fuel_codes = {code for code, _name in fuel_types}

        selected_fuel = (
            user.get(CONF_FUEL_TYPE)
            or existing_nickname.get(CONF_CHEAPEST_FUEL_TYPE)
            or self._flow_data.get(CONF_FUEL_TYPE)
            or suggested_fuel
        )
        if selected_fuel not in valid_fuel_codes:
            selected_fuel = suggested_fuel

        # Get radius in km, convert to meters for LocationSelector display
        selected_radius_km = existing_nickname.get(CONF_RADIUS_KM)
        if selected_radius_km is None:
            selected_radius_km = self._flow_data.get(CONF_RADIUS_KM, DEFAULT_RADIUS_KM)

        if user_input is not None and CONF_EXCLUDE_STRING in user_input:
            exclude_string = user_input[CONF_EXCLUDE_STRING]
        else:
            exclude_string = existing_nickname.get(
                CONF_EXCLUDE_STRING,
                self._flow_data.get(CONF_EXCLUDE_STRING, DEFAULT_EXCLUDE_STRING),
            )

        # The selector stores radius inside CONF_LOCATION in meters. If the current
        # form data does not already include that nested value, derive it from the
        # saved nickname radius in km for display.
        if (
            CONF_RADIUS_M not in suggested_location
            or suggested_location[CONF_RADIUS_M] is None
        ):
            suggested_location = {
                **suggested_location,
                CONF_RADIUS_M: DistanceConverter.convert(
                    selected_radius_km,
                    UnitOfLength.KILOMETERS,
                    UnitOfLength.METERS,
                ),
            }
        return vol.Schema(
            {
                vol.Required(
                    CONF_NICKNAME,
                    # "suggested" will also remind user of any invalid nickname entered
                    description={"suggested_value": nickname},
                ): TextSelector(),
                vol.Required(
                    CONF_LOCATION,
                    default=suggested_location,
                    description={"suggested_value": suggested_location},
                ): LocationSelector(
                    LocationSelectorConfig(
                        radius=True,
                    )
                ),
                vol.Required(
                    CONF_FUEL_TYPE,
                    default=selected_fuel,
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=fuel_options,
                        multiple=False,
                        sort=False,
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Optional(
                    CONF_EXCLUDE_STRING,
                    description={"suggested_value": exclude_string},
                ): TextSelector(),
            }
        )

    async def _get_station_list(
        self,
        lat: float,
        lon: float,
        radius_km: int = DEFAULT_RADIUS_KM,
        fuel_type: str = DEFAULT_FUEL_TYPE,
    ) -> dict[str, str]:
        """Return a list of nearby stations from API.

        The API appears to balance price/distance regardless of the sort by setting.
        In NSW E10-U91 returns the most reliable/sensible results for "cheap nearby".
        Store the fuel types available at each station for config entry (though not displayed)
        """

        errors = {}

        try:
            if not self.api:
                raise HomeAssistantError("API client not initialized")

            nearby = await self.api.get_fuel_prices_within_radius(
                latitude=lat,
                longitude=lon,
                radius=radius_km,
                fuel_type=fuel_type,
            )

            # E10-U91 combo returns multiple rows per station NOT multiple prices within a station
            seen: set[str] = set()
            unique_prices: list[StationPrice] = []

            self._station_lookup = {}

            for sp in nearby:
                st = sp.station
                fuel = sp.price.fuel_type
                station_code = st.code

                # Build station lookup with all fuel types
                if station_code not in self._station_lookup:
                    self._station_lookup[station_code] = {
                        "station_code": station_code,
                        "station_name": st.name,
                        "au_state": st.au_state,
                        CONF_STATION_FUEL_TYPES: [],
                    }

                if (
                    fuel
                    not in self._station_lookup[station_code][CONF_STATION_FUEL_TYPES]
                ):
                    self._station_lookup[station_code][CONF_STATION_FUEL_TYPES].append(
                        fuel
                    )

                # Only keep the first StationPrice per station for display list
                if station_code not in seen:
                    seen.add(station_code)
                    unique_prices.append(sp)

            self._nearby_station_prices = unique_prices[:STATION_LIST_LIMIT]

        except NSWFuelApiClientAuthError:
            errors["base"] = "auth"
        except NSWFuelApiClientError:
            errors["base"] = "connection"

        return errors


def _create_nickname_with_stations(
    entry: Mapping[str, Any],
    nickname: str,
    location: dict[str, Any] | None,
    stations: list[dict[str, Any]],
    radius_km: int = DEFAULT_RADIUS_KM,
    cheapest_fuel_type: str = DEFAULT_FUEL_TYPE,
    exclude_string: str = DEFAULT_EXCLUDE_STRING,
) -> dict[str, Any]:
    """Create a new nickname config entry block with stations and fuel."""

    new_entry = dict(entry)
    nicknames = dict(new_entry.get("nicknames", {}))

    if nickname in nicknames:
        raise ValueError("Nickname already exists")

    nicknames[nickname] = {
        CONF_LOCATION: location,
        CONF_RADIUS_KM: radius_km,
        CONF_CHEAPEST_FUEL_TYPE: cheapest_fuel_type,
        CONF_EXCLUDE_STRING: exclude_string,
        "stations": [
            {
                CONF_STATION_CODE: s[CONF_STATION_CODE],
                CONF_STATION_NAME: s[CONF_STATION_NAME],
                CONF_AU_STATE: s[CONF_AU_STATE],
                CONF_STATION_FUEL_TYPES: s[CONF_STATION_FUEL_TYPES],
            }
            for s in stations
        ],
    }

    new_entry["nicknames"] = nicknames
    return new_entry


def _add_stations_to_nickname(
    entry: Mapping[str, Any],
    nickname: str,
    location: dict[str, Any] | None,
    stations: list[dict[str, Any]],
    radius_km: int | None = None,
    cheapest_fuel_type: str | None = None,
    exclude_string: str | None = None,
) -> dict[str, Any]:
    """Add stations to an existing nickname. Update location etc if changed."""

    new_entry = dict(entry)
    nicknames = dict(new_entry.get("nicknames", {}))

    if nickname not in nicknames:
        raise ValueError("Nickname does not exist")

    nickname_block = dict(nicknames[nickname])
    existing_stations = list(nickname_block.get("stations", []))

    existing_station_index = {
        (st[CONF_STATION_CODE], st[CONF_AU_STATE]): st for st in existing_stations
    }

    for station in stations:
        key = (station[CONF_STATION_CODE], station[CONF_AU_STATE])

        if key not in existing_station_index:
            existing_stations.append(
                {
                    CONF_STATION_CODE: station[CONF_STATION_CODE],
                    CONF_AU_STATE: station[CONF_AU_STATE],
                    CONF_STATION_NAME: station[CONF_STATION_NAME],
                    CONF_STATION_FUEL_TYPES: [],
                }
            )

    nickname_block["stations"] = existing_stations

    if location is not None:
        nickname_block[CONF_LOCATION] = location
    if radius_km is not None:
        nickname_block[CONF_RADIUS_KM] = radius_km
    if cheapest_fuel_type is not None:
        nickname_block[CONF_CHEAPEST_FUEL_TYPE] = cheapest_fuel_type
    if exclude_string is not None:
        nickname_block[CONF_EXCLUDE_STRING] = exclude_string

    nicknames[nickname] = nickname_block
    new_entry["nicknames"] = nicknames

    return new_entry


def _add_fuel_to_stations(
    entry: Mapping[str, Any],
    nickname: str,
    stations: list[dict],
) -> dict:
    """Add fuel type to stations."""

    new_entry = copy.deepcopy(dict(entry))
    nicknames = dict(new_entry.get("nicknames", {}))

    if nickname not in nicknames:
        raise ValueError("Nickname does not exist")

    nickname_block = new_entry["nicknames"][nickname]
    existing_stations = list(nickname_block.get("stations", []))

    existing_station_index = {
        (st[CONF_STATION_CODE], st[CONF_AU_STATE]): st for st in existing_stations
    }

    for station in stations:
        key = (station[CONF_STATION_CODE], station[CONF_AU_STATE])

        if key not in existing_station_index:
            continue

        existing = dict(existing_station_index[key])

        fuels = set(existing.get(CONF_STATION_FUEL_TYPES, []))
        fuels.update(station.get(CONF_STATION_FUEL_TYPES, []))

        existing[CONF_STATION_FUEL_TYPES] = sorted(fuels)

        existing_station_index[key] = existing

    nickname_block["stations"] = list(existing_station_index.values())
    nicknames[nickname] = nickname_block
    new_entry["nicknames"] = nicknames

    return new_entry


def _remove_nickname_device_and_entities(
    hass: HomeAssistant,
    entry: config_entries.ConfigEntry,
    nickname: str,
) -> None:
    """Remove all entities and the HA device for an empty nickname/location."""
    device_registry = dr.async_get(hass)
    device_id = dr.async_get_device_id_by_identifier(
        hass,
        (DOMAIN, f"location_{nickname}"),
        config_entry_id=entry.entry_id,
    )
    if device_id is None:
        return

    entity_registry = er.async_get(hass)
    for entity_entry in er.async_entries_for_config_entry(
        entity_registry, entry.entry_id
    ):
        if entity_entry.device_id == device_id:
            entity_registry.async_remove(entity_entry.entity_id)

    device_registry.async_remove_device(device_id)


def _remove_station_entities(
    hass: HomeAssistant,
    entry: config_entries.ConfigEntry,
    nickname: str,
    station_code: int,
    au_state: str,
    fuel_types: list[str],
) -> None:
    """Remove entity-registry entries for removed station fuels."""
    if not fuel_types:
        return

    device_id = dr.async_get_device_id_by_identifier(
        hass,
        (DOMAIN, f"location_{nickname}"),
        config_entry_id=entry.entry_id,
    )
    if device_id is None:
        return

    entity_registry = er.async_get(hass)
    removed_suffixes = {
        f"_{station_code}_{au_state}_{fuel_type}" for fuel_type in fuel_types
    }

    for entity_entry in er.async_entries_for_config_entry(
        entity_registry, entry.entry_id
    ):
        if entity_entry.device_id != device_id:
            continue
        if any(
            entity_entry.unique_id.endswith(suffix)
            for suffix in removed_suffixes
        ):
            entity_registry.async_remove(entity_entry.entity_id)


def _update_configured_station(
    entry: Mapping[str, Any],
    nickname: str,
    station_code: int,
    fuel_types: list[str],
    *,
    remove_station: bool = False,
) -> dict[str, Any]:
    """Update configured fuels for one station, or remove that station."""
    new_entry = copy.deepcopy(dict(entry))
    nicknames = new_entry.get("nicknames", {})
    if nickname not in nicknames:
        raise ValueError("Nickname does not exist")

    nickname_block = nicknames[nickname]
    stations = list(nickname_block.get("stations", []))
    matching = [
        station for station in stations if station[CONF_STATION_CODE] == station_code
    ]
    if not matching:
        raise ValueError("Station does not exist")

    if remove_station:
        nickname_block["stations"] = [
            station
            for station in stations
            if station[CONF_STATION_CODE] != station_code
        ]
    else:
        for station in stations:
            if station[CONF_STATION_CODE] == station_code:
                station[CONF_STATION_FUEL_TYPES] = sorted(set(fuel_types))
                break
        nickname_block["stations"] = stations

    return new_entry


def _validate_location(location: dict[str, Any] | None) -> tuple[float, float, str]:
    """Return lat, long and state if valid and roughly within NSW/ACT/TAS or raise ValueError.

    V2 of API added support for TAS, ACT always treated as NSW.
    Other states using other APIs so supporting a new state would be a major change.
    """

    if location is None or not isinstance(location, dict):
        msg = "invalid_coordinates"
        raise ValueError(msg)
    try:
        lat = cv.latitude(location[CONF_LATITUDE])
        lon = cv.longitude(location[CONF_LONGITUDE])
    except Exception as err:
        msg = "invalid_coordinates"
        raise ValueError(msg) from err

    if not (LAT_SE_BOUND <= lat <= LAT_CAMERON_CORNER_BOUND) or not (
        LON_CAMERON_CORNER_BOUND <= lon <= LON_SE_BOUND
    ):
        msg = "invalid_coordinates"
        raise ValueError(msg)

    # Hardcode basic latitude test for now
    au_state = "TAS" if lat < LAT_TAS_N_BOUND else "NSW"

    return lat, lon, au_state


def _format_station_option(sp: StationPrice) -> str:
    """Return a user-friendly station label for the UI.

    TODO: Some long addresses are treated unkindly by the selector/UI
    """
    st = sp.station
    return f"{st.name} - {st.address} ({st.code})"


def _get_state_defaults(
    suggested_location: dict[str, Any],
) -> tuple[str, list[tuple[str, str]]]:
    """Get default fuel type and available fuel types for a location.

    NSW (& ACT) support combo codes (E10-U91), while TAS does appear to.
    API returns good results for E10-U91 in NSW.
    In TAS, E10-U91 returns NSW results, so we limit to U91 only.

    Returns:
        Tuple of (default_fuel_type, fuel_types_list)
    """
    latitude = suggested_location.get(CONF_LATITUDE)

    # Since we only support 2 states, just use lat for now
    if latitude is not None and latitude >= LAT_TAS_N_BOUND:
        return DEFAULT_FUEL_TYPE, list(ALL_FUEL_TYPES.items())

    return DEFAULT_FUEL_TYPE_NON_E10, [
        (code, name) for code, name in ALL_FUEL_TYPES.items() if "-" not in code
    ]
