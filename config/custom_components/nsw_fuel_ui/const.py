"""Constants for nsw_fuel_ui."""

from logging import Logger, getLogger

LOGGER: Logger = getLogger(__package__)

DOMAIN = "nsw_fuel_ui"
ATTRIBUTION = "Data provided by https://www.fuelcheck.nsw.gov.au/"
AUTH_URL = "https://api.onegov.nsw.gov.au/oauth/client_credential/accesstoken?grant_type=client_credentials"
BASE_URL = "https://api.onegov.nsw.gov.au"
REFERENCE_ENDPOINT = "/FuelCheckRefData/v2/fuel/lovs"
PRICE_ENDPOINT = "/FuelPriceCheck/v2/fuel/prices/station/{station_code}"
REF_DATA_REFRESH_DAYS = 30
