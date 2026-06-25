"""Application config loading."""
from __future__ import annotations

import json
import os


def _merge(base, override):
    """Return `base` updated with `override`; override wins on conflicts."""
    out = dict(base)
    out.update(override)
    return out


def _env_key(key):
    """Environment variable name for a config key (convention: APP_<UPPER>)."""
    return "APP_" + key.upper()


def _coerce(raw, like):
    """Coerce a string env value to the type of an existing value `like`."""
    if isinstance(like, bool):
        return raw.strip().lower() in ("1", "true", "yes", "on")
    if isinstance(like, int):
        return int(raw)
    if isinstance(like, float):
        return float(raw)
    return raw


def load_config(path):
    """Load configuration from a JSON file at `path`."""
    with open(path, encoding="utf-8") as f:
        return json.load(f)
