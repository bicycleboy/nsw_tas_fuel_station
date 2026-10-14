"""Read optional observers without propagating values or observer failures."""

from __future__ import annotations

from typing import Any

# Export bounds, not statements about provider token lifetimes or capacity.
MAX_COUNTER = (1 << 53) - 1
MAX_EXPIRY_SECONDS = 366 * 24 * 60 * 60


def diagnostic_value(owner: Any, name: str) -> Any:
    """Read a selected property; cancellation/system exits are not swallowed."""
    try:
        return getattr(owner, name, None)
    except Exception:  # noqa: BLE001 - isolate observers or classify acquisition failure
        return None


def diagnostic_int(owner: Any, name: str, maximum: int = MAX_COUNTER) -> int | None:
    """Accept only bounded non-negative built-in integers, excluding bool."""
    value = diagnostic_value(owner, name)
    return value if type(value) is int and 0 <= value <= maximum else None


def diagnostic_counts(
    owner: Any, name: str, keys: tuple[str, ...]
) -> dict[str, int] | None:
    """Copy only fixed keys from a plain dict; never coerce or inspect extras."""
    values = diagnostic_value(owner, name)
    if type(values) is not dict:
        return None
    try:
        selected = {key: values.get(key) for key in keys}
    except Exception:  # noqa: BLE001 - malformed keys may implement failing equality
        return None
    if not all(
        type(value) is int and 0 <= value <= MAX_COUNTER for value in selected.values()
    ):
        return None
    if sum(selected.values()) > MAX_COUNTER:
        return None
    return selected
