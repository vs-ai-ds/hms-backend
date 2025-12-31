"""
Common date/time utility functions for consistent date/time handling across the application

Storage: All dates are stored in UTC (ISO 8601 format) in the backend
Display: Dates can be converted to local timezone for display if needed

This ensures consistency across frontend and backend:
- Frontend sends UTC ISO strings to backend
- Backend stores dates in UTC
- Backend returns UTC ISO strings to frontend
"""

from datetime import datetime, timedelta, timezone


def as_utc(dt: datetime) -> datetime:
    """
    Convert dt to tz-aware UTC.
    If dt is naive, we treat it as UTC (consistent with existing behavior).

    Args:
        dt: datetime object (naive or timezone-aware)

    Returns:
        datetime object in UTC timezone
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def utc_now() -> datetime:
    """
    Get current UTC datetime.

    Returns:
        Current datetime in UTC timezone
    """
    return datetime.now(timezone.utc)


def parse_iso_string(iso_string: str) -> datetime:
    """
    Parse an ISO 8601 string to UTC datetime.
    Handles both with and without 'Z' suffix.

    Args:
        iso_string: ISO 8601 string (e.g., "2024-12-28T10:30:00.000Z" or "2024-12-28T10:30:00+00:00")

    Returns:
        datetime object in UTC timezone
    """
    # Replace 'Z' with '+00:00' for consistent parsing
    if iso_string.endswith("Z"):
        iso_string = iso_string[:-1] + "+00:00"

    dt = datetime.fromisoformat(iso_string)
    return as_utc(dt)


def round_to_next_15_minutes(dt: datetime) -> datetime:
    """
    Round datetime to the next 15-minute interval (00, 15, 30, 45).

    Args:
        dt: datetime object to round

    Returns:
        New datetime object with minutes rounded to next 15-minute interval
    """
    rounded = dt.replace(second=0, microsecond=0)
    minutes = rounded.minute
    rounded_minutes = ((minutes // 15) + 1) * 15

    if rounded_minutes >= 60:
        rounded = rounded.replace(minute=0) + timedelta(hours=1)
    else:
        rounded = rounded.replace(minute=rounded_minutes)

    return rounded


def round_to_nearest_15_minutes(dt: datetime) -> datetime:
    """
    Round datetime to the nearest 15-minute interval (00, 15, 30, 45).

    Args:
        dt: datetime object to round

    Returns:
        New datetime object with minutes rounded to nearest 15-minute interval
    """
    rounded = dt.replace(second=0, microsecond=0)
    minutes = rounded.minute
    rounded_minutes = round(minutes / 15) * 15

    if rounded_minutes >= 60:
        rounded = rounded.replace(minute=0) + timedelta(hours=1)
    else:
        rounded = rounded.replace(minute=int(rounded_minutes))

    return rounded


def is_valid_15_minute_interval(dt: datetime) -> bool:
    """
    Validate that a datetime has minutes in 15-minute intervals (00, 15, 30, 45).

    Args:
        dt: datetime object to validate

    Returns:
        True if minutes are in valid 15-minute interval, False otherwise
    """
    minutes = dt.minute
    return minutes in (0, 15, 30, 45)


def get_next_15_minute_slot(add_minutes: int = 0) -> datetime:
    """
    Get the next available 15-minute slot from now (for walk-in appointments).
    Rounds up to the next 15-minute interval (00, 15, 30, 45).

    Args:
        add_minutes: Additional minutes to add before rounding (default: 0)

    Returns:
        datetime object in UTC with the next available 15-minute slot
    """
    now = utc_now()
    if add_minutes > 0:
        now = now + timedelta(minutes=add_minutes)
    return round_to_next_15_minutes(now)
