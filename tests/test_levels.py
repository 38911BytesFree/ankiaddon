"""The `card_level` setting: prompt text, provider wiring, and config validation."""

from __future__ import annotations

from core.config import coerce_config
from core.gemini import GeminiProvider, _build_payload
from core.models import SourceImage
from core.prompts import DEFAULT_LEVEL, LEVELS, system_prompt
from core.provider import ProxyProvider, build_provider

_IMAGE = SourceImage(data=b"x", mime_type="image/jpeg")


def test_default_level_is_high_school():
    assert DEFAULT_LEVEL == "high_school"


def test_each_level_pitches_the_prompt_differently():
    prompts = {level: system_prompt(level) for level in LEVELS}
    assert "high school" in prompts["high_school"]
    assert "university" in prompts["university"]
    assert prompts["high_school"] != prompts["university"]


def test_prompt_has_no_unfilled_placeholders():
    for level in LEVELS:
        text = system_prompt(level)
        assert "{reader}" not in text and "{depth}" not in text


def test_unknown_level_falls_back_to_default():
    assert system_prompt("kindergarten") == system_prompt(DEFAULT_LEVEL)


def test_payload_carries_the_levels_prompt():
    payload = _build_payload(_IMAGE, "", "university")
    assert payload["systemInstruction"]["parts"][0]["text"] == system_prompt("university")


def test_build_provider_passes_level_to_gemini():
    provider = build_provider(
        {"backend": "gemini_direct", "api_key": "k", "model": "m", "card_level": "university"}
    )
    assert isinstance(provider, GeminiProvider)
    assert provider.level == "university"


def test_build_provider_defaults_level_when_unset():
    provider = build_provider({"backend": "gemini_direct", "api_key": "k", "model": "m"})
    assert provider.level == DEFAULT_LEVEL


def test_build_provider_passes_level_to_proxy():
    provider = build_provider(
        {"backend": "proxy", "proxy_url": "https://example.test/g", "card_level": "university"}
    )
    assert isinstance(provider, ProxyProvider)
    assert provider.level == "university"


def test_coerce_config_defaults_card_level():
    assert coerce_config({})["card_level"] == DEFAULT_LEVEL


def test_coerce_config_accepts_known_levels_loosely_spelled():
    assert coerce_config({"card_level": "University"})["card_level"] == "university"
    assert coerce_config({"card_level": "High School"})["card_level"] == "high_school"


def test_coerce_config_rejects_unknown_level():
    assert coerce_config({"card_level": "phd"})["card_level"] == DEFAULT_LEVEL
    assert coerce_config({"card_level": 3})["card_level"] == DEFAULT_LEVEL
