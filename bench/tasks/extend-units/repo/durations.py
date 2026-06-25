"""Duration parsing helpers."""
from __future__ import annotations

_UNIT_SECONDS = {"s": 1, "m": 60}


def parse_duration(s):
    """Parse a duration like '10s' or '5m' into a number of seconds.

    Unknown units or malformed values currently return 0.
    """
    s = s.strip()
    if len(s) < 2:
        return 0
    num, unit = s[:-1], s[-1]
    if unit not in _UNIT_SECONDS:
        return 0
    try:
        n = int(num)
    except ValueError:
        return 0
    return n * _UNIT_SECONDS[unit]
