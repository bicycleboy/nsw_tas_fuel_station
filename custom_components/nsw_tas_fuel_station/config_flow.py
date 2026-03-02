"""Config flow for NSW Fuel Check integration."""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any, cast

from nsw_tas_fuel import (
    NSWFuelApiClient,
    NSWFuelApiClientAuthError,
    NSWFuelApiClientError,
)
import voluptuous as vol

from homeassistant.config_entries import ConfigFlow
from homeassistant.const import CONF_CLIENT_ID, CONF_CLIENT_SECRET
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_create_clientsession
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.selector import (
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
from homeassistant.helpers.translation import async_get_translations
from homeassistant.util import slugify

from .const import (
    ALL_FUEL_TYPES,
    CONF_FUEL_TYPE,
    CONF_LOCATION,
    CONF_NICKNAME,
    CONF_SELECTED_STATIONS,
    DEFAULT_FUEL_TYPE,
    DEFAULT_FUEL_TYPE_NON_E10,
    DEFAULT_NICKNAME,
    DEFAULT_RADIUS_KM,
    DOMAIN,
    LAT_CAMERON_CORNER_BOUND,
    LAT_SE_BOUND,
    LAT_TAS_BOUND,
    LON_CAMERON_CORNER_BOUND,
    LON_SE_BOUND,
    STATION_LIST_LIMIT,
)

from .coordinator import state_default_fuel

if TYPE_CHECKING:
    from nsw_tas_fuel.client import StationPrice

    from homeassistant import config_entries

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
        self._last_form: dict[str, Any] | None = None
        self._nearby_station_prices: list[StationPrice] = []
        self._station_lookup: dict[int, dict[str, Any]] = {}
        self.api: NSWFuelApiClient | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Step 1 - Prompt for API credentials.

        API call get_fuel_prices_within_radius both validates credentials and gathers
        list of stations for step 2.
        The default path does not allow user to enter nickname or fuel type so
        use default and don't validate.  Location defaults to HA Home Zone.
        """
        errors: dict[str, str] = {}

        if user_input is None:
            return self.async_show_form(
                step_id="user",
                data_schema=self._build_user_schema(),
                errors=errors,
                last_step=False,
            )

        self._last_form = user_input
        self._flow_data.update(user_input)

        if not self._flow_data.get(CONF_NICKNAME):
            self._flow_data[CONF_NICKNAME] = DEFAULT_NICKNAME

        nickname = self._flow_data[CONF_NICKNAME]

        # Create API client, allows invalid home zone to error to advanced path
        session = async_create_clientsession(self.hass)
        api = NSWFuelApiClient(
            session=session,
            client_id=self._flow_data[CONF_CLIENT_ID],
            client_secret=self._flow_data[CONF_CLIENT_SECRET],
        )

        self.api = api

        location = self._flow_data.get(CONF_LOCATION)
        if not isinstance(location, dict):
            location = {
                "latitude": getattr(self.hass.config, "latitude", None),
                "longitude": getattr(self.hass.config, "longitude", None),
            }
            self._flow_data[CONF_LOCATION] = location

        # Default Home zone may be outside NSW/ACT/TAS
        try:
            lat, lon, au_state = self._validate_location(self._flow_data[CONF_LOCATION])
        except ValueError as err:
            errors["base"] = str(err)
            _LOGGER.debug("Invalid location: %s", err)
            return self.async_show_form(
                step_id="advanced_options",
                data_schema=self._build_advanced_options_schema(
                    self._last_form or self._flow_data
                ),
                errors=errors,
            )

        if not self._flow_data.get(CONF_FUEL_TYPE):
            self._flow_data[CONF_FUEL_TYPE] = state_default_fuel(au_state)

        # Store metadata to save coordinator additional api calls
        nicknames = self._flow_data.setdefault("nicknames", {})
        nickname_data = nicknames.setdefault(nickname, {})
        nickname_data["location"] = {"latitude": lat, "longitude": lon}

        # First network API call in config flow
        errors = await self._get_station_list(lat, lon, self._flow_data[CONF_FUEL_TYPE])
        if errors:
            return self.async_show_form(
                step_id="user",
                data_schema=self._build_user_schema(self._last_form or self._flow_data),
                errors=errors,
            )

        return await self.async_step_station_select()

    async def async_step_station_select(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Step 2 - allow user to select stations for a nickname/location.

        This step entered either via default path or via advanced options.
        Build or update a config entry for selected stations with
        attributes required to create sensors without additional API calls.
        In the advanced path we want the user to be able to add fuel types
        to existing stations and stations to existing nicknames.
        """
        errors: dict[str, str] = {}

        adv_label = await self._get_advanced_option_label()

        # First time through we want to show the user "Find more stations..."
        if user_input is None:
            return self.async_show_form(
                step_id="station_select",
                data_schema=self._build_station_schema(
                    self._nearby_station_prices, advanced_label=adv_label
                ),
                errors=errors,
            )

        selected_codes_str = user_input.get(CONF_SELECTED_STATIONS, [])
        self._last_form = user_input

        # User selected "Find more..."
        if selected_codes_str == ["__advanced__"]:
            return await self.async_step_advanced_options()

        # User selected stations *and* "Find more....", ignore "Find more..."
        selected_codes_str = [
            code for code in selected_codes_str if code != "__advanced__"
        ]

        selected_codes = [int(x) for x in selected_codes_str]

        # User did not select anything
        if not selected_codes:
            errors["base"] = "no_stations"
            return self.async_show_form(
                step_id="station_select",
                data_schema=self._build_station_schema(
                    self._nearby_station_prices,
                    user_input=self._last_form,
                    advanced_label="",
                ),
                errors=errors,
            )

        #
        # User selected stations, create or update config entry
        #
        self._flow_data.update(user_input)

        nickname = self._flow_data[CONF_NICKNAME]
        selected_fuel_code = self._flow_data[CONF_FUEL_TYPE]

        updating_entry: config_entries.ConfigEntry | None = None

        for entry in self._async_current_entries():
            if nickname in entry.data.get("nicknames", {}):
                updating_entry = entry
                break

        if updating_entry is None:
            return await self._create_new_config_entry(
                nickname, selected_codes, selected_fuel_code
            )

        return await self._update_existing_entry(
            updating_entry,
            nickname,
            selected_codes,
            selected_fuel_code,
            self._nearby_station_prices,
        )

    async def async_step_advanced_options(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Step 3 - optional advanced configuration.

        - allow the user to enter non-default nickname, fuel, location.
        - allow the user to create a new nickname to group stations.
        - support the creation of "cheapest near ..." sensors.
        - allow the user to add stations to an existing location.
        - allow the user to add additional fuel types to existing stations
        """
        errors: dict[str, str] = {}

        if user_input is None:
            return self.async_show_form(
                step_id="advanced_options",
                data_schema=self._build_advanced_options_schema(),
                errors=errors,
            )

        self._last_form = user_input

        # "Cheapest near" sensors must be distinguishable to the user in the UI so the
        # nickname is included in the sensor name/id, hence validate
        nickname = user_input.get(CONF_NICKNAME, DEFAULT_NICKNAME)
        if not nickname or not re.match(r"^[A-Za-z0-9_-]+$", nickname):
            errors["nickname"] = "invalid_nickname"

        if errors:
            return self.async_show_form(
                step_id="advanced_options",
                data_schema=self._build_advanced_options_schema(
                    self._last_form or self._flow_data
                ),
                errors=errors,
            )

        self._flow_data[CONF_NICKNAME] = nickname

        # Locations outside NSW/TAS currently unsupported
        try:
            lat, lon, au_state = self._validate_location(user_input.get(CONF_LOCATION))
        except ValueError as err:
            errors["base"] = str(err)

            return self.async_show_form(
                step_id="advanced_options",
                data_schema=self._build_advanced_options_schema(
                    self._last_form or self._flow_data
                ),
                errors=errors,
            )

        # Schema ensures a valid fuel value
        fuel_type = user_input.get(CONF_FUEL_TYPE)
        self._flow_data[CONF_FUEL_TYPE] = fuel_type

        # Save location in case user returns to advanced options
        self._flow_data[CONF_LOCATION] = {"latitude": lat, "longitude": lon}

        # Initialise nickname structure
        nicknames = self._flow_data.setdefault("nicknames", {})
        nickname_data = nicknames.setdefault(nickname, {})
        nickname_data["location"] = {"latitude": lat, "longitude": lon}

        # Network API call for neaby stations for user entered location & fuel type
        # API results can be unexpected for fuel/location combinations
        # debug logging may highlight API results vs bugs
        errors = await self._get_station_list(lat, lon, fuel_type)

        if errors:
            return self.async_show_form(
                step_id="advanced_options",
                data_schema=self._build_advanced_options_schema(
                    self._last_form or self._flow_data
                ),
                errors=errors,
            )

        return await self.async_step_station_select()

    async def _create_new_config_entry(
        self,
        nickname: str,
        selected_codes: list[int],
        selected_fuel_code: str,
    ) -> config_entries.ConfigFlowResult:
        """Create a config entry for a new nickname.

        Store metadata from API to save coordinator additional API calls.
        In the default path, hardcode fuel types E10, U91
        """
        unique_id = f"{DOMAIN}_{slugify(nickname)}"
        await self.async_set_unique_id(unique_id)
        self._abort_if_unique_id_configured()

        nickname_data = self._flow_data["nicknames"][nickname]

        entry_data = {
            CONF_CLIENT_ID: self._flow_data[CONF_CLIENT_ID],
            CONF_CLIENT_SECRET: self._flow_data[CONF_CLIENT_SECRET],
            "nicknames": {
                nickname: {
                    "location": nickname_data["location"],
                    "stations": [
                        {
                            "station_code": code,
                            "au_state": self._station_lookup[code]["au_state"],
                            "station_name": self._station_lookup[code]["station_name"],
                            "fuel_types": _split_combo_fuel_code(selected_fuel_code),
                        }
                        for code in selected_codes
                    ],
                }
            },
        }

        return self.async_create_entry(
            title="NSW Fuel Check",
            data=entry_data,
        )

    async def _update_existing_entry(
        self,
        updating_entry: config_entries.ConfigEntry,
        nickname: str,
        selected_station_codes: list[int],
        selected_fuel_code: str,
        available_stations: list[StationPrice],
    ) -> config_entries.ConfigFlowResult:
        """Merge user selection with existing config entry.

        Allows user to add stations to an existing nickname/location
        or add additional fuel types to existing station.
        """
        existing_config_entry = dict(updating_entry.data)
        config_entry_nicknames = dict(existing_config_entry.get("nicknames", {}))
        existing_stations: list[dict] = config_entry_nicknames[nickname].get(
            "stations", []
        )
        existing_sensors: set[tuple[int, str, str]] = set()

        for st in existing_stations:
            for ft in st.get("fuel_types", []):
                existing_sensors.add((st["station_code"], st["au_state"], ft))

            selected_fuels = _split_combo_fuel_code(selected_fuel_code)

            # Alert user to existing sensor
            for selected_station_code in selected_station_codes:
                station_lookup = self._station_lookup[selected_station_code]

                for fuel in selected_fuels:
                    sensor_key = (
                        selected_station_code,
                        station_lookup["au_state"],
                        fuel,
                    )

                    if sensor_key in existing_sensors:
                        return self.async_show_form(
                            step_id="station_select",
                            data_schema=self._build_station_schema(
                                available_stations,
                                user_input=self._last_form,
                                advanced_label=await self._get_advanced_option_label(),
                            ),
                            errors={"base": "sensor_exists"},
                            description_placeholders={
                                "station": station_lookup["station_name"],
                                "fuel": fuel,
                            },
                        )

        merged_stations_map: dict[tuple[int, str], dict[str, Any]] = {
            (st["station_code"], st["au_state"]): dict(st) for st in existing_stations
        }

        # merge station to nickname or additional fuel to station
        for selected_station_code in selected_station_codes:
            station_lookup = self._station_lookup[selected_station_code]
            station_state_key = (selected_station_code, station_lookup["au_state"])

            if station_state_key in merged_stations_map:
                fuels = set(
                    merged_stations_map[station_state_key].get("fuel_types", [])
                )

                fuels.update(_split_combo_fuel_code(selected_fuel_code))
                merged_stations_map[station_state_key]["fuel_types"] = sorted(fuels)
            else:
                merged_stations_map[station_state_key] = {
                    "station_code": selected_station_code,
                    "au_state": station_lookup["au_state"],
                    "station_name": station_lookup["station_name"],
                    "fuel_types": _split_combo_fuel_code(selected_fuel_code),
                }

        merged_stations = list(merged_stations_map.values())

        # Update the nickname with merged stations
        # Location may be updated so cheapest sensors will be
        # updated based on last selected location
        nick_location = self._flow_data.get("nicknames", {}).get(nickname, {}).get(
            "location"
        ) or config_entry_nicknames.get(nickname, {}).get("location")

        config_entry_nicknames[nickname] = {
            "location": nick_location,
            "stations": merged_stations,
        }

        new_data = {
            CONF_CLIENT_ID: existing_config_entry[CONF_CLIENT_ID],
            CONF_CLIENT_SECRET: existing_config_entry[CONF_CLIENT_SECRET],
            "nicknames": config_entry_nicknames,
        }

        self.hass.config_entries.async_update_entry(updating_entry, data=new_data)

        return self.async_abort(reason="updated_existing")

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

        config_entry = {}

        if user_input:
            config_entry = user_input
        else:
            entries = self._async_current_entries()
            if entries:
                config_entry = entries[0].data

        return self.add_suggested_values_to_schema(schema, config_entry)

    def _build_station_schema(
        self,
        stations: list[StationPrice],
        user_input: dict[str, Any] | None = None,
        advanced_label: str = "...",
    ) -> vol.Schema:
        """Build config flow UI schema for the station selection list/dropdown."""
        user = user_input or self._flow_data

        # In absence of "advanced options" button, add "Find more stations..." to list
        options: list[SelectOptionDict] = [
            cast(SelectOptionDict, {"value": "__advanced__", "label": advanced_label}),
            *[
                cast(
                    SelectOptionDict,
                    {
                        "value": str(sp.station.code),
                        "label": _format_station_option(sp),
                    },
                )
                for sp in stations
            ],
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
                    default=user.get(CONF_SELECTED_STATIONS, []),
                ): select_selector,
            }
        )

    def _build_advanced_options_schema(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> vol.Schema:
        """Build UI schema for advanced options: nickname, location, fuel type."""
        user = user_input or self._flow_data

        # Set nickname keeping any invalid nicknames for user correction
        nickname = (
            (user or {}).get(CONF_NICKNAME)
            or self._flow_data.get(CONF_NICKNAME)
            or DEFAULT_NICKNAME
        )

        try:
            suggested_location = self._flow_data.get(CONF_LOCATION)

            if not suggested_location:
                suggested_location = {
                    "latitude": getattr(self.hass.config, "latitude", None),
                    "longitude": getattr(self.hass.config, "longitude", None),
                }

            suggested_fuel, fuel_types = _get_state_defaults(suggested_location)
            return vol.Schema(
                {
                    vol.Required(
                        CONF_NICKNAME,
                        default=nickname,
                        # "suggested" will also remind user of invalid nickname entered
                        description={"suggested_value": nickname},
                    ): TextSelector(),
                    vol.Required(
                        CONF_LOCATION,
                        default=suggested_location,
                        description={"suggested_value": suggested_location},
                    ): LocationSelector(
                        LocationSelectorConfig(
                            radius=False,
                        )
                    ),
                    vol.Required(
                        CONF_FUEL_TYPE,
                        default=suggested_fuel,
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                SelectOptionDict(
                                    value=code,
                                    label=name,
                                )
                                for code, name in fuel_types
                            ],
                            multiple=False,
                            sort=False,
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    ),
                }
            )
        except Exception as e:
            _LOGGER.debug("Exception in _build_advanced_options_schema: %s", e)
            raise

    def _validate_location(
        self, location: dict[str, Any] | None
    ) -> tuple[float, float, str]:
        """Return lat & long if valid and roughly within NSW/TAS or raise ValueError."""
        if location is None or not isinstance(location, dict):
            msg = "invalid_coordinates"
            raise ValueError(msg)
        try:
            lat = cv.latitude(location["latitude"])
            lon = cv.longitude(location["longitude"])
        except Exception as err:
            msg = "invalid_coordinates"
            raise ValueError(msg) from err

        if not (LAT_SE_BOUND <= lat <= LAT_CAMERON_CORNER_BOUND) or not (
            LON_CAMERON_CORNER_BOUND <= lon <= LON_SE_BOUND
        ):
            msg = "invalid_coordinates"
            raise ValueError(msg)

        # Hardcode basic latitude test for now
        au_state = "TAS" if lat < LAT_TAS_BOUND else "NSW"

        return lat, lon, au_state

    async def _get_station_list(
        self,
        lat: float,
        lon: float,
        fuel_type: str,
    ) -> dict[str, str]:
        """Return a list of nearby stations.

        The API appears to balance price/distance regardless of
        the sort by setting.
        U91 returns the most reliable/sensible results for "nearby".
        """
        errors = {}

        try:
            if not self.api:
                raise HomeAssistantError("API client not initialized")

            nearby = await self.api.get_fuel_prices_within_radius(
                latitude=lat,
                longitude=lon,
                radius=DEFAULT_RADIUS_KM,
                fuel_type=fuel_type,
            )

            # E10-U91 returns multiple rows per station
            seen: set[str] = set()
            unique_prices = []
            for sp in nearby:
                station_code = sp.station.code

                if station_code in seen:
                    continue

                seen.add(station_code)
                unique_prices.append(sp)

            self._nearby_station_prices = unique_prices[:STATION_LIST_LIMIT]

            # Build station lookup by station code
            self._station_lookup = {}
            for sp in self._nearby_station_prices:
                st = sp.station

                self._station_lookup[st.code] = {
                    "station_code": st.code,
                    "station_name": st.name,
                    "au_state": st.au_state,
                }

        except NSWFuelApiClientAuthError:
            errors["base"] = "auth"
        except NSWFuelApiClientError:
            errors["base"] = "connection"

        return errors

    async def _get_advanced_option_label(self) -> str:
        """Return the locale language text for the advanced option."""
        key = f"component.{DOMAIN}.config.step.station_select.data.advanced_option"
        translations = await async_get_translations(
            self.hass,
            self.hass.config.language,
            "config",
            {DOMAIN},
        )
        return translations.get(key) or "More stations..."


def _format_station_option(sp: StationPrice) -> str:
    """Return a user-friendly station label for the UI.

    TODO: Some long addresses are treated unkindly by the selector/UI
    """
    st = sp.station
    return f"{st.name} - {st.address} ({st.code})"


def _split_combo_fuel_code(selected_fuel_code: str | None) -> list[str]:
    """Convert a fuel code string (e.g., 'E10-U91') into a list of fuels.

    The API returns the best results in NSW with E10-U91 so need special handling

    Returns:
        List of individual fuel codes, e.g. ['E10', 'U91'].
        If input is None or empty, returns an empty list.
    """
    if not selected_fuel_code:
        return []
    return [fuel.strip() for fuel in selected_fuel_code.split("-") if fuel.strip()]


def _get_state_defaults(
    suggested_location: dict[str, Any],
) -> tuple[str, list[tuple[str, str]]]:
    """Get default fuel type and available fuel types for a location.

    NSW supports combo codes (E10-U91), while TAS does not.
    API returns good results for E10-U91 in NSW.
    In TAS, E10-U91 returns NSW results, so we limit to U91 only.

    Args:
        suggested_location: Dict with at least 'latitude' key

    Returns:
        Tuple of (default_fuel_type, fuel_types_list)
    """
    latitude = suggested_location.get("latitude")

    # NSW supports combo codes, since we only support 2 states, just use lat for now
    if latitude is not None and latitude >= LAT_TAS_BOUND:
        return DEFAULT_FUEL_TYPE, list(ALL_FUEL_TYPES.items())

    # TAS default to U91
    return DEFAULT_FUEL_TYPE_NON_E10, [
        (code, name) for code, name in ALL_FUEL_TYPES.items() if "-" not in code
    ]
