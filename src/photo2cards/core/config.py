"""Validation and normalization of add-on configuration.

Ensures hand-edited or malformed settings loaded from Anki's meta.json are
coerced to their expected types and bounds, preventing unexpected runtime errors.
"""

from __future__ import annotations

import json
import os
import re

_CONFIG_JSON = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config.json")


def load_default_config() -> dict:
    """Read defaults from the shipped config.json to ensure a single source of truth."""
    try:
        with open(_CONFIG_JSON, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {
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
            "attach_source_quote": True,
            "attach_source_image": False,
            "extra_tags": ["photo2cards"],
        }


DEFAULT_CONFIG: dict = load_default_config()


def coerce_config(raw: dict | None, defaults: dict | None = None) -> dict:
    """Return a clean config dict with types validated and numbers clamped."""
    base_defaults = defaults if isinstance(defaults, dict) else DEFAULT_CONFIG
    if not isinstance(raw, dict):
        return dict(base_defaults)

    res = dict(raw)

    # 1. backend
    backend = str(res.get("backend") or "").strip()
    if backend not in ("gemini_direct", "proxy"):
        backend = str(base_defaults.get("backend", "gemini_direct"))
    res["backend"] = backend

    # 2. string fields
    for field in ("api_key", "model", "proxy_url", "proxy_token", "default_notetype"):
        val = res.get(field)
        res[field] = str(val).strip() if val is not None else str(base_defaults.get(field, ""))
    if not res["default_notetype"]:
        res["default_notetype"] = str(base_defaults.get("default_notetype", "Basic"))

    # 3. numeric fields
    def _coerce_int(key: str, default: int, min_val: int, max_val: int) -> int:
        val = res.get(key)
        try:
            num = int(val)
        except (TypeError, ValueError):
            return default
        return max(min_val, min(max_val, num))

    res["max_image_edge"] = _coerce_int(
        "max_image_edge", int(base_defaults.get("max_image_edge", 1600)), 400, 4096
    )
    res["jpeg_quality"] = _coerce_int(
        "jpeg_quality", int(base_defaults.get("jpeg_quality", 85)), 10, 100
    )
    res["requests_per_minute"] = _coerce_int(
        "requests_per_minute", int(base_defaults.get("requests_per_minute", 15)), 1, 1000
    )

    deck_id_raw = res.get("default_deck_id")
    try:
        res["default_deck_id"] = int(deck_id_raw) if deck_id_raw is not None else 0
    except (TypeError, ValueError):
        res["default_deck_id"] = int(base_defaults.get("default_deck_id", 0))

    # 4. booleans: handle both native booleans and string representations
    # from hand-edited configs
    for bool_field in ("attach_source_quote", "attach_source_image"):
        val = res.get(bool_field)
        if val is None:
            res[bool_field] = bool(base_defaults.get(bool_field, False))
        elif isinstance(val, str):
            res[bool_field] = val.strip().lower() in ("true", "1", "yes", "on")
        else:
            res[bool_field] = bool(val)

    # 5. extra_tags (must be list of non-empty strings)
    tags_raw = res.get("extra_tags")
    cleaned_tags: list[str] = []
    if isinstance(tags_raw, str):
        for part in re.split(r"[\s,]+", tags_raw.strip()):
            cleaned = re.sub(r"\s+", "_", part.strip())[:100]
            if cleaned:
                cleaned_tags.append(cleaned)
    elif isinstance(tags_raw, (list, tuple, set)):
        for item in tags_raw:
            if item is not None:
                cleaned = re.sub(r"\s+", "_", str(item).strip())[:100]
                if cleaned:
                    cleaned_tags.append(cleaned)
    else:
        fallback = base_defaults.get("extra_tags", ["photo2cards"])
        if isinstance(fallback, (list, tuple)):
            cleaned_tags = list(fallback)
        else:
            cleaned_tags = ["photo2cards"]

    res["extra_tags"] = cleaned_tags

    return res
