"""Config flow for NSW Fuel Check integration."""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any, cast

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlow
from homeassistant.const import CONF_CLIENT_ID, CONF_CLIENT_SECRET
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import (
    async_get_clientsession,
)
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
from nsw_tas_fuel import (
    NSWFuelApiClient,
    NSWFuelApiClientAuthError,
    NSWFuelApiClientError,
)

from .const import (
    ALL_FUEL_TYPES,
    CONF_AU_STATE,
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


    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """No options, just verify credentials and a list of stations near home zone.

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

        await self.async_set_unique_id(client_id)
        self._abort_if_unique_id_configured()

        nickname = DEFAULT_NICKNAME

        location = {
            "latitude": getattr(self.hass.config, "latitude", None),
            "longitude": getattr(self.hass.config, "longitude", None),
        }

        # Create the API client and store credentials before validating the location so we can error to advanced path
        session = async_get_clientsession(self.hass)
        self.api = NSWFuelApiClient(
            session=session,
            client_id=client_id,
            client_secret=client_secret,
        )
        self._flow_data.update({
            CONF_CLIENT_ID: user_input[CONF_CLIENT_ID],
            CONF_CLIENT_SECRET: user_input[CONF_CLIENT_SECRET],
        })

        # Validate HA Home location
        try:
            lat, lon, au_state = _validate_location(location)
        except ValueError as err:
            errors["base"] = str(err)
            _LOGGER.debug("Invalid HA Home location: %s", err)

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
            fuel_type,
        )

        if errors:
            return self.async_show_form(
                step_id="user",
                data_schema=self._build_user_schema(self._last_form),
                errors=errors,
            )

        # Store state for next step
        self._flow_data.update({
            CONF_CLIENT_ID: client_id,
            CONF_CLIENT_SECRET: client_secret,
            CONF_NICKNAME: nickname,
            CONF_AU_STATE: au_state,
            CONF_FUEL_TYPE: fuel_type,
            CONF_LOCATION: {
                "latitude": lat,
                "longitude": lon,
            },
        })

        return await self.async_step_station_select()


    async def async_step_station_select(
        self,
        user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Present user with a list of nearby stations to select from and handle selection."""

        errors: dict[str, str] = {}

        # Show form if user_input is not provided
        if user_input is None:
            return self.async_show_form(
                step_id="station_select",
                data_schema=self._build_station_schema(self._nearby_station_prices),  # fix 2nd param to use self._nearby_station_prices
                errors=errors,
            )

        nickname = self._flow_data.get(CONF_NICKNAME, DEFAULT_NICKNAME)
        selected_fuel_code = self._flow_data.get(CONF_FUEL_TYPE)

        # Convert selection to list of ints
        selected_stations = [int(x) for x in user_input.get(CONF_SELECTED_STATIONS, [])]

        if not selected_stations:
            errors["base"] = "no_stations"
            return self.async_show_form(
                step_id="station_select",
                data_schema=self._build_station_schema(self._nearby_station_prices),
                errors=errors,
            )

        # Save selection in flow state
        self._flow_data.update(user_input)

        # Build station payload
        stations_payload = [
            {
                "station_code": code,
                "au_state": self._station_lookup[code]["au_state"],
                "station_name": self._station_lookup[code]["station_name"],
            }
            for code in selected_stations
        ]

        # Handle reconfigure: update existing entry
        if self.source == config_entries.SOURCE_RECONFIGURE:
            existing_data = self._config_entry.data
            existing_nicknames = existing_data.get("nicknames", {})

            if nickname not in existing_nicknames:
                # Create new nickname block
                new_data = _create_nickname_with_stations(
                    existing_data,
                    nickname,
                    self._flow_data[CONF_LOCATION],
                    stations_payload,
                    selected_fuel_code,
                )
            else:
                # Add stations if needed
                new_data = _add_stations_to_nickname(existing_data, nickname, stations_payload)
                # Add fuel type to those stations
                new_data = _add_fuel_to_stations(new_data, nickname, stations_payload, selected_fuel_code)

                self.hass.config_entries.async_update_entry(self._config_entry, data=new_data)
                return self.async_abort(reason="reconfigured")

        # Create a new config entry (first-time setup)
        return await self._create_new_config_entry(nickname, selected_stations)



    async def async_step_reconfigure(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Reconfigure an existing entry."""

        self._config_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )

        if self._config_entry is None:
            return self.async_abort(reason="unknown_entry")

        self._flow_data = dict(self._config_entry.data)

        if self.api is None:
            self.api = NSWFuelApiClient(
                session=async_get_clientsession(self.hass),
                client_id=self._config_entry.data[CONF_CLIENT_ID],
                client_secret=self._config_entry.data[CONF_CLIENT_SECRET],
            )

        return await self.async_step_advanced_options(user_input)


    async def _create_new_config_entry(
        self,
        nickname: str,
        selected_stations: list[int]
    ) -> FlowResult:
        """Create a config entry for a new nickname.

        Store metadata from API to save coordinator additional API calls.
        In the default path, hardcode fuel types E10, U91
        """

        entry_data = {
            CONF_CLIENT_ID: self._flow_data[CONF_CLIENT_ID],
            CONF_CLIENT_SECRET: self._flow_data[CONF_CLIENT_SECRET],
            "nicknames": {
                nickname: {
                    "location": self._flow_data[CONF_LOCATION],
                    "stations": [
                        {
                            "station_code": code,
                            "au_state": self._station_lookup[code]["au_state"],
                            "station_name": self._station_lookup[code]["station_name"],
                            "fuel_types": self._station_lookup[code]["fuel_types"]
                        }
                        for code in selected_stations
                    ],
                }
            },
        }

        return self.async_create_entry(title="NSW Fuel Check", data=entry_data,)


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


    async def async_step_advanced_options(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 1 of OptionsFlow: choose nickname, location and fuel."""
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

        # Only NSW/ACT/TAS supported
        try:
            lat, lon, au_state = _validate_location(
                user_input.get(CONF_LOCATION)
            )
        except ValueError as err:
            errors["base"] = str(err)

        # Schema ensures validity)
        fuel_type = user_input.get(CONF_FUEL_TYPE)

        if errors:
            return self.async_show_form(
                step_id="advanced_options",
                data_schema=self._build_advanced_options_schema(user_input),
                errors=errors,
            )

        self._flow_data.update({
            CONF_NICKNAME: nickname,
            CONF_LOCATION: {"latitude": lat, "longitude": lon},
            CONF_AU_STATE: au_state,
            CONF_FUEL_TYPE: fuel_type,
        })

        # Network API call
        errors = await self._get_station_list(lat, lon, fuel_type)
        if errors:
            return self.async_show_form(
                step_id="advanced_options",
                data_schema=self._build_advanced_options_schema(user_input),
                errors=errors,
            )

        return await self.async_step_station_select()

    def _build_station_schema(
        self,
        stations: list[StationPrice],
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
            for sp in stations
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


    async def _get_station_list(
        self,
        lat: float,
        lon: float,
        fuel_type: str,
    ) -> dict[str, str]:
        """Return a list of nearby stations.

        The API appears to balance price/distance regardless of
        the sort by setting.
        In NSW E10-U91 returns the most reliable/sensible results for "cheap nearby".
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
            unique_prices: list[StationPrice] = []

            self._station_lookup = {}

            for sp in nearby:
                st = sp.station
                fuel = sp.price.fuel_type
                code = st.code

                # Build station lookup with all fuel types
                if code not in self._station_lookup:
                    self._station_lookup[code] = {
                        "station_code": code,
                        "station_name": st.name,
                        "au_state": st.au_state,
                        "fuel_types": [],
                    }

                if fuel not in self._station_lookup[code]["fuel_types"]:
                    self._station_lookup[code]["fuel_types"].append(fuel)

                # Only keep the first StationPrice per station for display list
                if code not in seen:
                    seen.add(code)
                    unique_prices.append(sp)

            self._nearby_station_prices = unique_prices[:STATION_LIST_LIMIT]

        except NSWFuelApiClientAuthError:
            errors["base"] = "auth"
        except NSWFuelApiClientError:
            errors["base"] = "connection"

        return errors


def _create_nickname_with_stations(
    data: dict,
    nickname: str,
    location: dict,
    stations: list[dict],
    fuel_code: str,
) -> dict:
    """Create a brand new nickname block with stations and fuel.

    Assumes nickname does NOT already exist.
    """

    new_data = dict(data)
    nicknames = dict(new_data.get("nicknames", {}))

    if nickname in nicknames:
        raise ValueError("Nickname already exists")

    nicknames[nickname] = {
        "location": location,
        "stations": [
            {
                "station_code": s["station_code"],
                "station_name": s["station_name"],
                "au_state": s["au_state"],
                "fuel_types": _split_combo_fuel_code(fuel_code),
            }
            for s in stations
        ],
    }

    new_data["nicknames"] = nicknames
    return new_data


def _add_stations_to_nickname(
    data: dict,
    nickname: str,
    stations: list[dict],
) -> dict:
    """Add stations to an existing nickname."""

    new_data = dict(data)
    nicknames = dict(new_data.get("nicknames", {}))

    if nickname not in nicknames:
        raise ValueError("Nickname does not exist")

    nickname_block = dict(nicknames[nickname])
    existing_stations = list(nickname_block.get("stations", []))

    station_index = {
        (s["station_code"], s["au_state"]): s
        for s in existing_stations
    }

    for station in stations:
        key = (station["station_code"], station["au_state"])

        if key not in station_index:
            existing_stations.append(
                {
                    "station_code": station["station_code"],
                    "au_state": station["au_state"],
                    "station_name": station["station_name"],
                    "fuel_types": [],
                }
            )

    nickname_block["stations"] = existing_stations
    nicknames[nickname] = nickname_block
    new_data["nicknames"] = nicknames

    return new_data

def _add_fuel_to_stations(
    data: dict,
    nickname: str,
    stations: list[dict],
    fuel_code: str,
) -> dict:
    """Add fuel type to stations."""

    new_data = dict(data)
    nicknames = dict(new_data.get("nicknames", {}))

    if nickname not in nicknames:
        raise ValueError("Nickname does not exist")

    nickname_block = dict(nicknames[nickname])
    existing_stations = list(nickname_block.get("stations", []))

    station_index = {
        (s["station_code"], s["au_state"]): s
        for s in existing_stations
    }

    new_fuel_codes = _split_combo_fuel_code(fuel_code)

    for station in stations:
        key = (station["station_code"], station["au_state"])

        if key not in station_index:
            continue

        existing = dict(station_index[key])

        fuels = set(existing.get("fuel_types", []))
        fuels.update(new_fuel_codes)

        existing["fuel_types"] = sorted(fuels)

        station_index[key] = existing

    nickname_block["stations"] = list(station_index.values())
    nicknames[nickname] = nickname_block
    new_data["nicknames"] = nicknames

    return new_data


def _validate_location(location: dict[str, Any] | None
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
    if latitude is not None and latitude >= LAT_TAS_N_BOUND:
        return DEFAULT_FUEL_TYPE, list(ALL_FUEL_TYPES.items())

    # TAS default to U91
    return DEFAULT_FUEL_TYPE_NON_E10, [
        (code, name) for code, name in ALL_FUEL_TYPES.items() if "-" not in code
    ]

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
