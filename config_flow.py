"""Config flow for NSW Fuel integration."""

from __future__ import annotations

import os
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_CLIENT_ID, CONF_CLIENT_SECRET
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from nsw_fuel import NSWFuelApiClient, NSWFuelApiClientAuthError, NSWFuelApiClientError

from .const import DOMAIN, LOGGER

CONF_STATION_CODE = "station_code"

class NSWFuelConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow for NSW Fuel."""

    VERSION = 1

    def _build_schema(self, user_input: dict | None = None) -> vol.Schema:
        user_input = user_input or {}
        suggested_client_id = os.getenv("NSWFUELCHECKAPI_KEY", "")
        suggested_client_secret = os.getenv("NSWFUELCHECKAPI_SECRET", "")

        return vol.Schema(
            {
                vol.Required(
                    CONF_CLIENT_ID,
                    default=user_input.get(CONF_CLIENT_ID, vol.UNDEFINED),
                    description={"suggested_value": suggested_client_id},
                ): selector.TextSelector(),
                vol.Required(
                    CONF_CLIENT_SECRET,
                    default=user_input.get(CONF_CLIENT_SECRET, vol.UNDEFINED),
                    description={"suggested_value": suggested_client_secret},
                ): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
                ),
                vol.Required(
                    CONF_STATION_CODE,
                    default=user_input.get(CONF_STATION_CODE, vol.UNDEFINED),
                    description={"suggested_value": ""},
                ): selector.TextSelector(),
            }
        )

    async def async_step_user(
        self,
        user_input: dict[str, Any] | None = None) -> config_entries.ConfigFlowResult:
        """Handle the initial step."""
        errors = {}

        # First time entering the form — show it
        if user_input is None:
            return self.async_show_form(
                step_id="user",
                data_schema=self._build_schema(),
                errors=errors,
            )

        # We have real user input — validate it
        try:
            await self._test_credentials(
                client_id=user_input[CONF_CLIENT_ID],
                client_secret=user_input[CONF_CLIENT_SECRET],
            )
        except NSWFuelApiClientAuthError:
            errors["base"] = "auth"
        except NSWFuelApiClientError:
            errors["base"] = "connection"

        if errors:
            # Re-show the form with the submitted values
            return self.async_show_form(
                step_id="user",
                data_schema=self._build_schema(user_input),
                errors=errors,
            )

        # Success — create entry
        unique_id = f"{user_input[CONF_CLIENT_ID]}-{user_input[CONF_STATION_CODE]}"
        await self.async_set_unique_id(unique_id)
        self._abort_if_unique_id_configured()

        return self.async_create_entry(
            title=f"NSW Fuel ({user_input[CONF_STATION_CODE]})",
            data=user_input,
        )

    async def _test_credentials(self, client_id: str, client_secret: str) -> None:
        """Validate credentials by fetching a token."""
        session = async_create_clientsession(self.hass)
        client = NSWFuelApiClient(
            session=session, client_id=client_id, client_secret=client_secret
        )
        LOGGER.debug("_test_credentials called")
        # Getting a token validates the credentials
        try:
            await client.get_fuel_prices_for_station("18798")
        except NSWFuelApiClientAuthError as err:
            LOGGER.error("Invalid NSW Fuel API credentials: %s", err)
            raise
        except NSWFuelApiClientError as err:
            LOGGER.error("Error communicating with NSW Fuel API: %s", err)
            raise
