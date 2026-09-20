"""Local wall-clock formatting for people; never use these values on the wire."""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

# Only format documented display fields, never arbitrary text, IDs or credentials.
ISO_TIME_FIELDS = frozenset({
    "updatedAt", "observedAt", "lastSeenAt", "checkedAt", "lastPushAt",
})
_AWARE_ISO = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})"
)


def format_local_time(value: object) -> str:
    """Render an aware datetime/ISO instant in the OS timezone, or '-' if invalid.

    Reject timezone-less strings rather than guessing whether they are UTC or local.
    astimezone() applies the timezone's offset at the instant (including DST).
    """
    if isinstance(value, str):
        if not _AWARE_ISO.fullmatch(value):
            return "-"
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return "-"
    if not isinstance(value, datetime) or value.tzinfo is None:
        return "-"
    try:
        if value.utcoffset() is None:
            return "-"
        local = value.astimezone()
        return f"{local.year:04d}-{local:%m-%d %H:%M:%S}"
    except (ValueError, OverflowError, OSError):
        return "-"


def display_times(value: Any) -> Any:
    """Copy a status/report document and format known time fields for display only."""
    if isinstance(value, dict):
        return {
            key: format_local_time(item) if key in ISO_TIME_FIELDS else display_times(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [display_times(item) for item in value]
    return value
