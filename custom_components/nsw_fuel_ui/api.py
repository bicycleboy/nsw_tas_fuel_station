"""Asynchronous API client for the NSW Fuel API."""

from __future__ import annotations

import base64
import json
import logging
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from aiohttp import (
    ClientConnectionError,
    ClientError,
    ClientResponseError,
    ClientSession,
    ClientTimeout,
)

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

    async def async_get_token(self) -> str | None:
        """Get or refresh OAuth2 token from the NSW Fuel API."""
        _LOGGER.debug("async_get_token called")
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
            _LOGGER.debug("Instance of NSWFuelApiClient created: id=%s", id(self))

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
                    try:
                        result = await resp.json()
                    except ClientConnectionError as err:
                        _LOGGER.exception("Connection dropped while parsing token")
                        msg = "Connection lost during token fetch"
                        raise NSWFuelApiClientError(msg) from err
                else:
                    _LOGGER.warning(
                        "Expected application/json, got %s", resp.content_type
                    )
                    try:
                        result = json.loads(text)
                    except json.JSONDecodeError as err:
                        msg = "Failed to parse token response"
                        raise NSWFuelApiClientError(msg) from err

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
            access_token = result.get("access_token")
            if access_token is not None:
                self._token = access_token
                expires_in = int(result.get("expires_in", 3600))
                self._token_expiry = now + expires_in
                _LOGGER.debug("Token acquired; expires in %s seconds", expires_in)
            else:
                self._token = None
                msg = "No access_token in NSW Fuel token response"
                raise NSWFuelApiClientError(msg)


        return self._token

    async def _async_request(
        self, path: str, params: dict[str, Any] | None = None
    ) -> Any:
        """Perform authorized GET request."""
        token = await self.async_get_token()
        if not token:
            msg = "No access token available"
            raise NSWFuelApiClientError(msg)

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "apikey": self._client_id,
            "TransactionID": str(uuid.uuid4()),  # unique per request
            "RequestTimestamp": datetime.now(UTC).isoformat(),  # UTC ISO8601
        }
        url = f"{BASE_URL}{path}"

        token_preview = (
            f"{token[:6]}...{token[-4:]}" if isinstance(token, str) else "<not-str>"
        )
        _LOGGER.debug(
            "Requesting %s with headers=%s",
            url,
            {
                "Authorization": f"Bearer {token_preview}",
                "Accept": "application/json",
            },
        )

        try:
            async with self._session.get(
                url, headers=headers, params=params, timeout=ClientTimeout(total=30)
            ) as resp:
                if resp.status == HTTP_UNAUTHORIZED:
                    body = await resp.text()
                    _LOGGER.debug("401 response body: %s", body)
                    _LOGGER.warning("Oauth token expired unexpectedly, refreshing...")

                    self._token = None
                    token = await self.async_get_token()
                    if not token:
                        msg = "Failed to refresh Oauth token"
                        raise NSWFuelApiClientAuthError(msg)  # noqa: TRY301

                    headers["Authorization"] = f"Bearer {token}"
                    token_preview = f"{token[:6]}...{token[-4:]}"
                    _LOGGER.debug(
                        "Retrying %s with headers=%s",
                        url,
                        {
                            "Authorization": f"Bearer {token_preview}",
                            "Accept": "application/json",
                        },
                    )

                    async with self._session.get(
                        url,
                        headers=headers,
                        params=params,
                        timeout=ClientTimeout(total=30),
                    ) as retry:
                        retry.raise_for_status()
                        return await retry.json(content_type=None)

                resp.raise_for_status()
                return await resp.json(content_type=None)

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
