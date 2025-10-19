"""Config flow for NSW Fuel integration."""

from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_CLIENT_ID, CONF_CLIENT_SECRET
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .api import NSWFuelApiClient, NSWFuelApiClientAuthError, NSWFuelApiClientError
from .const import DOMAIN, LOGGER

CONF_STATION_CODE = "station_code"


class NSWFuelConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow for NSW Fuel."""

    VERSION = 1

    async def async_step_user(
        self,
        user_input: dict | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Handle a flow initialized by the user."""
        errors: dict[str, str] = {}

        # Provide defaults for easier testing ** during development **
        user_input = user_input or {}

        if CONF_CLIENT_ID in user_input and CONF_CLIENT_SECRET in user_input:
            try:
                await self._test_credentials(
                    client_id=user_input[CONF_CLIENT_ID],
                    client_secret=user_input[CONF_CLIENT_SECRET],
                )
            except NSWFuelApiClientAuthError as err:
                LOGGER.warning("Authentication failed: %s", err)
                errors["base"] = "auth"
            except NSWFuelApiClientError as err:
                LOGGER.error("Error connecting to NSW Fuel API: %s", err)
                errors["base"] = "connection"
            else:
                # Use station_code as part of the unique ID for this config entry
                unique_id = (
                    f"{user_input[CONF_CLIENT_ID]}-{user_input[CONF_STATION_CODE]}"
                )
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"NSW Fuel ({user_input[CONF_STATION_CODE]})",
                    data=user_input,
                )
        data_schema = {
            vol.Required(
                CONF_CLIENT_ID,
                default=user_input.get(CONF_CLIENT_ID, vol.UNDEFINED),
                description={                            # remove after development
                    "suggested_value": "wyAQv0evvBkKuJLbfq3xcK90inra6q2m"
                },
            ): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
            ),
            vol.Required(
                CONF_CLIENT_SECRET,
                default=user_input.get(CONF_CLIENT_SECRET, vol.UNDEFINED),
                description={
                    "suggested_value": "jsrJnkqi8GIwipu"  # remove after development
                },
            ): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
            ),
            vol.Required(
                CONF_STATION_CODE,
                default=user_input.get(CONF_STATION_CODE, vol.UNDEFINED),
                description={"suggested_value": "18798"},  # remove after development
            ): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
            ),
        }

        # Show the form with selectors and defaults
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(data_schema),
            errors=errors,
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
            await client.async_get_token()
        except NSWFuelApiClientAuthError as err:
            LOGGER.error("Invalid NSW Fuel API credentials: %s", err)
            raise
        except NSWFuelApiClientError as err:
            LOGGER.error("Error communicating with NSW Fuel API: %s", err)
            raise
