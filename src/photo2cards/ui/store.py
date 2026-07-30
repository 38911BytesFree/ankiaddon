"""Read/write the add-on's config through Anki's add-on manager.

Anki persists this in the add-on's `meta.json`. Plain text — see config.md.
"""

from __future__ import annotations

import json
import os

from aqt import mw

#: Anki keys config by the add-on's top-level package name, which is the folder
#: name on disk. Derive it rather than hardcoding, so a renamed folder still works.
ADDON_PACKAGE = __name__.split(".")[0]

_CONFIG_JSON = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config.json")


def _packaged_defaults() -> dict:
    """Read the shipped config.json as the source of defaults.

    Anki already requires this file, and hand-maintaining a second copy of the
    same dict here means a new setting silently behaves one way on a fresh
    install and another on an upgrade. One file, one answer.
    """
    try:
        with open(_CONFIG_JSON, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        # Never let a missing or malformed file stop the add-on loading; the
        # per-key `.get(...)` fallbacks at each call site still apply.
        return {}


DEFAULTS = _packaged_defaults()


def get_config() -> dict:
    """Current config, with defaults filled in for any missing key.

    Merging against DEFAULTS means a user upgrading from an older version never
    hits a KeyError on a setting that didn't exist when their meta.json was written.
    """
    stored = mw.addonManager.getConfig(ADDON_PACKAGE) or {}
    config = dict(DEFAULTS)
    config.update(stored)
    return config


def save_config(config: dict) -> None:
    mw.addonManager.writeConfig(ADDON_PACKAGE, config)


def update_config(**changes) -> dict:
    config = get_config()
    config.update(changes)
    save_config(config)
    return config


def is_configured() -> bool:
    config = get_config()
    if config.get("backend") == "proxy":
        return bool((config.get("proxy_url") or "").strip())
    return bool((config.get("api_key") or "").strip() and (config.get("model") or "").strip())
