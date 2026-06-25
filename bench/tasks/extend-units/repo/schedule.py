"""Scheduling helpers built on duration parsing."""
from __future__ import annotations

from durations import parse_duration


def slots(spec):
    """Parse a comma-separated duration spec into a list of second counts.

    Pieces that are invalid (i.e. parse to 0) are skipped.
    """
    out = []
    for piece in spec.split(","):
        secs = parse_duration(piece)
        if secs:
            out.append(secs)
    return out
