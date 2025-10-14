"""Asynchronous API client for the NSW Fuel API."""

from __future__ import annotations

import base64
import logging
import time
from typing import Any

from aiohttp import ClientError, ClientResponseError, ClientSession

from .const import AUTH_URL, BASE_URL, PRICE_ENDPOINT, REFERENCE_ENDPOINT

_LOGGER = logging.getLogger(__name__)
HTTP_UNAUTHORIZED = 401


class NSWFuelApiClientError(Exception):
    """General API error."""


class NSWFuelApiClientAuthError(NSWFuelApiClientError):
    """Authentication failure."""


class NSWFuelApiClient:
    """API client for NSW FuelCheck."""

    def __init__(
        self, session: ClientSession, client_id: str, client_secret: str
    ) -> None:
        """Initialize with aiohttp session and client credentials."""
        self._session = session
        self._client_id = client_id
        self._client_secret = client_secret
        self._token: str | None = None
        self._token_expiry: float = 0

    async def _async_get_token(self) -> str:
        """Get or refresh OAuth2 token from the NSW Fuel API."""
        now = time.time()

        # Refresh if no token or it will expire soon
        if not self._token or now > (self._token_expiry - 60):
            _LOGGER.debug("Refreshing NSW Fuel API token")

            params = {"grant_type": "client_credentials"}
            # Base64 encode client_id:client_secret
            auth_str = f"{self._client_id}:{self._client_secret}"
            auth_bytes = auth_str.encode("utf-8")
            auth_b64 = base64.b64encode(auth_bytes).decode("utf-8")
            headers = {
                "Accept": "application/json",
                "Authorization": f"Basic {auth_b64}",
            }

            try:
                async with self._session.get(
                    AUTH_URL, params=params, headers=headers
                ) as resp:
                    text = await resp.text()
                    _LOGGER.debug(
                        "Token response status=%s, content_type=%s, params=%s",
                        resp.status,
                        resp.content_type,
                        {"grant_type": params["grant_type"]},  # redact secret
                    )
                resp.raise_for_status()

                # Some NSW APIs mislabel JSON as x-www-form-urlencoded
                if "application/json" in resp.content_type:
                    result = await resp.json()
                else:
                    _LOGGER.warning("Falling back to JSON parse for token response")
                    result = json.loads(text)

            except ClientResponseError as err:
                if err.status == HTTP_UNAUTHORIZED:
                    msg = "Invalid NSW Fuel API credentials"
                    raise NSWFuelApiClientAuthError(msg) from err
                msg = f"Token request failed with status {err.status}: {err.message}"
                raise NSWFuelApiClientError(msg) from err

            except OSError as err:
                msg = f"Network error fetching NSW Fuel token: {err}"
                raise NSWFuelApiClientError(msg) from err

            # Parse result and cache token
            self._token = result.get("access_token")
            expires_in = int(result.get("expires_in", 3600))
            self._token_expiry = now + expires_in
            _LOGGER.debug("Token acquired; expires in %s seconds", expires_in)

        return self._token

    async def _async_request(
        self, path: str, params: dict[str, Any] | None = None
    ) -> Any:
        """Perform authorized GET request."""
        token = await self._async_get_token()
        headers = {"Authorization": f"Bearer {token}"}
        url = f"{BASE_URL}{path}"

        try:
            async with self._session.get(
                url, headers=headers, params=params, timeout=30
            ) as resp:
                if resp.status == HTTP_UNAUTHORIZED:
                    _LOGGER.warning("Token expired unexpectedly, refreshing...")
                    self._token = None
                    # Try once more with a new token
                    token = await self._async_get_token()
                    headers["Authorization"] = f"Bearer {token}"
                    async with self._session.get(
                        url, headers=headers, params=params, timeout=30
                    ) as retry:
                        retry.raise_for_status()
                        return await retry.json()

                resp.raise_for_status()
                return await resp.json()

        except ClientResponseError as err:
            if err.status == HTTP_UNAUTHORIZED:
                msg = "Authentication failed during request"
                raise NSWFuelApiClientAuthError(msg) from err
            msg = f"HTTP error {err.status}: {err.message}"
            raise NSWFuelApiClientError(msg) from err

        except ClientError as err:
            msg = f"Connection error: {err}"
            raise NSWFuelApiClientError(msg) from err

        except Exception as err:
            msg = f"Unexpected error: {err}"
            raise NSWFuelApiClientError(msg) from err

    async def async_get_reference_data(self) -> dict[str, Any]:
        """Fetch reference data (weekly)."""
        return await self._async_request(REFERENCE_ENDPOINT)

    async def async_get_station_price(self, station_code: str) -> dict[str, Any]:
        """Fetch station price (daily)."""
        return await self._async_request(
            PRICE_ENDPOINT.format(station_code=station_code)
        )
