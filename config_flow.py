"""Config flow for NSW Fuel integration."""

from __future__ import annotations

import os

from typing import TYPE_CHECKING, Any

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.config_entries import ConfigFlow
from homeassistant.const import CONF_CLIENT_ID, CONF_CLIENT_SECRET
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from nsw_fuel import (
    NSWFuelApiClient,
    NSWFuelApiClientAuthError,
    NSWFuelApiClientError,
)

from .const import (
    ATTRIBUTION,
    DOMAIN,
    LAT_CAMERON_CORNER_BOUND,
    LAT_SE_BOUND,
    LOGGER,
    LON_CAMERON_CORNER_BOUND,
    LON_SE_BOUND,
    VALID_STATES,
    DEFAULT_STATE,
    SENSOR_FUEL_TYPES,
)

if TYPE_CHECKING:
    from homeassistant import config_entries
    from nsw_fuel.client import StationPrice



CONF_SELECTED_STATIONS = "selected_station_codes"
CONF_LATITUDE = "latitude"
CONF_LONGITUDE = "longitude"

DEFAULT_RADIUS_METERS = 10  # 10 km
STATION_LIST_LIMIT = 20



def _format_station_option(sp: StationPrice) -> str:
    """Return a user-friendly station label for the UI."""
    st = sp.station
    return f"{st.name} - {st.address} ({st.code})"


class NSWFuelConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for NSW Fuel."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._user_inputs: dict[str, Any] = {}
        self._nearby_station_prices: list[StationPrice] = []
        self._station_info: dict[int, dict[str, Any]] = {}

        self._lat: float | None = None
        self._lon: float | None = None

    # ---------------------------------------------------------------
    # User Step (Credentials + Coordinates)
    # ---------------------------------------------------------------

    def _build_user_schema(self, user_input: dict | None = None) -> vol.Schema:
        """Form: API credentials + coordinates."""
        user = user_input or {}

        if user_input is None:
            suggested_client_id = os.getenv("NSWFUELCHECKAPI_KEY", "")
            suggested_client_secret = os.getenv("NSWFUELCHECKAPI_SECRET", "")
            suggested_lat = str(getattr(self.hass.config, "latitude", ""))
            suggested_lon = str(getattr(self.hass.config, "longitude", ""))
        else:
            suggested_client_id = user.get(CONF_CLIENT_ID, "")
            suggested_client_secret = user.get(CONF_CLIENT_SECRET, "")
            suggested_lat = user.get(CONF_LATITUDE, "")
            suggested_lon = user.get(CONF_LONGITUDE, "")

        return vol.Schema(
            {
                vol.Required(
                    CONF_CLIENT_ID,
                    default=user.get(CONF_CLIENT_ID, vol.UNDEFINED),
                    description={"suggested_value": suggested_client_id},
                ): selector.TextSelector(),
                vol.Required(
                    CONF_CLIENT_SECRET,
                    default=user.get(CONF_CLIENT_SECRET, vol.UNDEFINED),
                    description={"suggested_value": suggested_client_secret},
                ): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
                ),
                vol.Required(
                    CONF_LATITUDE,
                    default=user.get(CONF_LATITUDE, suggested_lat),
                    description={"suggested_value": suggested_lat},
                ): selector.TextSelector(),
                vol.Required(
                    CONF_LONGITUDE,
                    default=user.get(CONF_LONGITUDE, suggested_lon),
                    description={"suggested_value": suggested_lon},
                ): selector.TextSelector(),
            }
        )

    # ---------------------------------------------------------------
    # Station Selection Step
    # ---------------------------------------------------------------

    def _build_station_schema(
        self, user_input: dict[str, Any] | None = None
    ) -> vol.Schema:
        user = user_input or {}

        options = [
            {
                "label": _format_station_option(sp),
                "value": str(sp.station.code),
            }
            for sp in self._nearby_station_prices
        ]

        try:
            select_selector = selector.selector(
                {
                    "select": {
                        "options": options,
                        "mode": "dropdown",
                        "multiple": True,
                        "sort": False,
                    }
                }
            )
        except Exception as err:
            LOGGER.error("Error building select selector: %s", err)
            raise

        LOGGER.debug("_build_station_schema exit")

        return vol.Schema(
            {
                vol.Required(
                    CONF_SELECTED_STATIONS,
                    default=user.get(CONF_SELECTED_STATIONS, []),
                ): select_selector,
            }
        )

    # ---------------------------------------------------------------
    # Flow Step: User Input
    # ---------------------------------------------------------------

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle the initial user step in config flow."""
        errors: dict[str, str] = {}

        # ---------------------------------------------------------
        # Initial form load
        # ---------------------------------------------------------
        if user_input is None:
            return self.async_show_form(
                step_id="user",
                data_schema=self._build_user_schema(),
                errors=errors,
                description_placeholders={"attribution": ATTRIBUTION},
            )

        # Store user entries (lat/lon + credentials)
        self._user_inputs.update(user_input)

        # ---------------------------------------------------------
        # Latitude / longitude validation
        # ---------------------------------------------------------
        try:
            lat = cv.latitude(user_input[CONF_LATITUDE])
            lon = cv.longitude(user_input[CONF_LONGITUDE])
        except vol.Invalid:
            errors["base"] = "invalid_coordinates"
            return self.async_show_form(
                step_id="user",
                data_schema=self._build_user_schema(user_input),
                errors=errors,
            )

        # Validate NSW bounding box
        if not (LAT_SE_BOUND <= lat <= LAT_CAMERON_CORNER_BOUND) or not (
            LON_CAMERON_CORNER_BOUND <= lon <= LON_SE_BOUND
        ):
            errors["base"] = "invalid_coordinates"
            return self.async_show_form(
                step_id="user",
                data_schema=self._build_user_schema(user_input),
                errors=errors,
            )

        self._lat = lat
        self._lon = lon

        # ---------------------------------------------------------
        # Validate API credentials by fetching nearby stations
        # ---------------------------------------------------------
        session = async_create_clientsession(self.hass)
        client = NSWFuelApiClient(
            session=session,
            client_id=user_input[CONF_CLIENT_ID],
            client_secret=user_input[CONF_CLIENT_SECRET],
        )

        try:
            LOGGER.debug("Fetching nearby stations for authentication check")

            # Uses U91 + small radius to confirm credentials & starting point
            nearby: list[StationPrice] = await client.get_fuel_prices_within_radius(
                latitude=lat,
                longitude=lon,
                radius=DEFAULT_RADIUS_METERS,
                fuel_type="U91",
            )

            # Keep list small for UI responsiveness
            self._nearby_station_prices = nearby[:STATION_LIST_LIMIT]

            # ---------------------------------------------------------
            # Build minimal station_info dict
            #
            # Do NOT include code/brand/name/address under "location".
            # Do NOT include latitude/longitude — coordinator fetches that later.
            #
            # coordinator._async_update_data() will use this to create Station()
            # via Station.deserialize(), so all required keys must be present.
            # ---------------------------------------------------------
            self._station_info = {}

            for sp in self._nearby_station_prices:
                station = sp.station

                # Infer state via address text (NSW / ACT / VIC)
                state = next(
                    (s for s in VALID_STATES if s in station.address),
                    DEFAULT_STATE,
                )

                # Store clean minimal info (structure used by Station.deserialize)
                self._station_info[station.code] = {
                    "stationid": station.ident,
                    "brand": station.brand,
                    "code": station.code,
                    "name": station.name,
                    "address": station.address,
                    # Coordinator requires location to contain lat/lon only
                    "location": {
                        "latitude": station.latitude,
                        "longitude": station.longitude,
                    },
                    # State is safe to store — deserialize simply ignores it
                    "state": state,
                }

        except NSWFuelApiClientAuthError:
            errors["base"] = "auth"
            return self.async_show_form(
                step_id="user",
                data_schema=self._build_user_schema(user_input),
                errors=errors,
            )

        except NSWFuelApiClientError as err:
            LOGGER.error("NSW Fuel API error: %s", err)
            errors["base"] = "connection"
            return self.async_show_form(
                step_id="user",
                data_schema=self._build_user_schema(user_input),
                errors=errors,
            )

        # ---------------------------------------------------------
        # Move to station selection step
        # ---------------------------------------------------------
        return await self.async_step_station_select()

    # ---------------------------------------------------------------
    # Flow Step: Station Selection
    # ---------------------------------------------------------------

    async def async_step_station_select(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle station selection step."""
        errors: dict[str, str] = {}
        LOGGER.debug("station_select entered")

        if user_input is None:
            return self.async_show_form(
                step_id="station_select",
                data_schema=self._build_station_schema(),
                errors=errors,
            )

        selected_codes_str = user_input.get(CONF_SELECTED_STATIONS, [])
        selected_codes = [int(code) for code in selected_codes_str]

        if not selected_codes:
            errors["base"] = "no_stations"
            return self.async_show_form(
                step_id="station_select",
                data_schema=self._build_station_schema(user_input),
                errors=errors,
            )

        # --- Unique ID change ---
        # Previously unique_id was client_id + station code.
        # Now it uses station state (lowercase) + station code for uniqueness.
        # This avoids collisions across states when the same station code appears.
        state = self._station_info[selected_codes[0]]["state"].lower()
        unique_id = f"{state}_{selected_codes[0]}"

        await self.async_set_unique_id(unique_id)
        self._abort_if_unique_id_configured()

        # --- Build config entry unchanged ---
        client_id = self._user_inputs[CONF_CLIENT_ID]
        entry_data = {
            CONF_CLIENT_ID: client_id,
            CONF_CLIENT_SECRET: self._user_inputs[CONF_CLIENT_SECRET],
            CONF_LATITUDE: self._lat,
            CONF_LONGITUDE: self._lon,
            CONF_SELECTED_STATIONS: selected_codes,
            "station_info": {code: self._station_info[code] for code in selected_codes},
        }

        title = f"NSW Fuel ({self._station_info[selected_codes[0]]['name']})"
        return self.async_create_entry(title=title, data=entry_data)
