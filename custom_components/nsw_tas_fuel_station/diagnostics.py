"""Diagnostics support for NSW Fuel Check."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .data import NSWFuelConfigEntry
from .diagnostic_values import (
    MAX_EXPIRY_SECONDS,
    diagnostic_counts,
    diagnostic_int,
    diagnostic_value,
)


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: NSWFuelConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry without exposing credentials."""
    domain_data = hass.data.get(DOMAIN)
    coordinator = domain_data.get(entry.entry_id) if type(domain_data) is dict else None
    api = diagnostic_value(coordinator, "api")
    http_counts = diagnostic_counts(
        api, "http_request_counts", ("oauth", "data", "retries")
    )
    http_total = (
        http_counts["oauth"] + http_counts["data"] if http_counts is not None else None
    )

    return {
        "api_request_accounting": {
            "last_refresh_api_operations": diagnostic_counts(
                coordinator,
                "last_refresh_api_operations",
                ("favorite_station", "cheapest_nearby"),
            ),
            "session_api_operations_total": diagnostic_int(
                coordinator, "api_operations_total"
            ),
            "http_request_counts": http_counts,
            "session_http_requests_total": http_total,
            "last_token_expires_in": diagnostic_int(
                api, "last_token_expires_in", MAX_EXPIRY_SECONDS
            ),
        }
    }
