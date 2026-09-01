"""Parsing and formatting of time shifts and UTC offsets."""

from __future__ import annotations

import re

_OFFSET_RE = re.compile(r"^(?P<sign>[+-])(?P<hours>\d{1,2}):(?P<minutes>[0-5]\d)$")
_SHIFT_RE = re.compile(
    r"^(?P<sign>[+-])(?P<hours>\d{1,4}):(?P<minutes>[0-5]\d)(?::(?P<seconds>[0-5]\d))?$"
)

MAX_OFFSET_MINUTES = 14 * 60


def parse_offset(text: str) -> int:
    """
    Parse a UTC offset like `-08:00` or `+09:30` into minutes.

    The sign is mandatory: an unsigned value cannot be told apart from a typo,
    and guessing here silently produces wrong timestamps.
    """
    raw = text.strip()
    if raw.upper() == "Z":
        return 0

    match = _OFFSET_RE.match(raw)
    if not match:
        raise ValueError(
            f"Invalid UTC offset {text!r}. Use ±HH:MM, e.g. -08:00, +09:30 or Z."
        )

    minutes = int(match["hours"]) * 60 + int(match["minutes"])
    if minutes > MAX_OFFSET_MINUTES:
        raise ValueError(f"UTC offset {text!r} is out of range (max ±14:00).")
    return -minutes if match["sign"] == "-" else minutes


def format_offset(minutes: int) -> str:
    """Format an offset in minutes as `±HH:MM`."""
    sign = "-" if minutes < 0 else "+"
    minutes = abs(minutes)
    return f"{sign}{minutes // 60:02d}:{minutes % 60:02d}"


def parse_shift(text: str) -> int:
    """Parse a manual shift like `+01:00:00` or `-00:30:00` into seconds."""
    match = _SHIFT_RE.match(text.strip())
    if not match:
        raise ValueError(
            f"Invalid shift {text!r}. Use ±HH:MM:SS, e.g. +01:00:00 or -00:30:00."
        )

    total = (
        int(match["hours"]) * 3600
        + int(match["minutes"]) * 60
        + int(match["seconds"] or 0)
    )
    return -total if match["sign"] == "-" else total


def compute_shift_seconds(
    shift: str | None,
    from_offset: str | None,
    to_offset: str | None,
) -> int:
    """
    Resolve the requested shift in seconds.

    An explicit `--shift` wins; otherwise the shift is `to_offset - from_offset`.
    """
    if shift:
        return parse_shift(shift)

    if from_offset and to_offset:
        return (parse_offset(to_offset) - parse_offset(from_offset)) * 60

    if from_offset or to_offset:
        raise ValueError(
            "--from-offset and --to-offset must be given together "
            "(or use --shift instead)."
        )

    raise ValueError("Provide either --shift or both --from-offset and --to-offset.")


def format_shift(seconds: int) -> str:
    """Format a shift in seconds as `±HH:MM:SS`."""
    sign = "-" if seconds < 0 else "+"
    seconds = abs(seconds)
    return f"{sign}{seconds // 3600:02d}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"


def exiftool_shift_expr(seconds: int) -> str:
    """
    Build an ExifTool shift expression such as `+=0:0:0 1:00:00`.

    The explicit `Y:M:D h:m:s` form is used because ExifTool interprets shorter
    forms positionally, which makes `1:00:00` ambiguous.
    """
    operator = "-=" if seconds < 0 else "+="
    seconds = abs(seconds)
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{operator}0:0:0 {hours}:{minutes:02d}:{secs:02d}"
