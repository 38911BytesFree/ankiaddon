"""Read/write the add-on's config through Anki's add-on manager.

Anki persists this in the add-on's `meta.json`. Plain text — see config.md.
"""

from __future__ import annotations

from aqt import mw

#: Anki keys config by the add-on's top-level package name, which is the folder
#: name on disk. Derive it rather than hardcoding, so a renamed folder still works.
ADDON_PACKAGE = __name__.split(".")[0]

DEFAULTS = {
    "backend": "gemini_direct",
    "api_key": "",
    "model": "",
    "proxy_url": "",
    "proxy_token": "",
    "max_image_edge": 1600,
    "jpeg_quality": 85,
    "requests_per_minute": 15,
    "default_deck_id": 0,
    "default_notetype": "Basic",
    "attach_source_image": True,
    "extra_tags": ["photo2cards"],
}


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
