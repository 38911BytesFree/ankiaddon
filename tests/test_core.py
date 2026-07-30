"""Tests for the pure-logic layer. No Anki required."""

import json

import pytest

from core.errors import (
    AuthError,
    BlockedError,
    ConfigError,
    ProviderError,
    RateLimitError,
    ResponseFormatError,
)
from core.gemini import _extract_text, _raise_for_error_payload, parse_cards
from core.models import Card
from core.provider import build_provider
from core.ratelimit import RateLimiter

# --------------------------------------------------------------------------- #
# Card parsing
# --------------------------------------------------------------------------- #


def _reply(cards):
    return json.dumps({"cards": cards})


def test_parses_a_well_formed_card():
    cards = parse_cards(
        _reply(
            [
                {
                    "front": "What is the powerhouse of the cell?",
                    "back": "The mitochondrion",
                    "tags": ["cell_biology", "organelles"],
                    "source_quote": "The mitochondrion is the powerhouse of the cell.",
                }
            ]
        )
    )
    assert len(cards) == 1
    assert cards[0].front.startswith("What is")
    assert cards[0].tags == ["cell_biology", "organelles"]
    assert cards[0].selected is True


def test_drops_cards_missing_a_side():
    cards = parse_cards(
        _reply(
            [
                {"front": "Q", "back": "A", "tags": [], "source_quote": ""},
                {"front": "", "back": "orphaned answer", "tags": [], "source_quote": ""},
                {"front": "no answer", "back": "   ", "tags": [], "source_quote": ""},
            ]
        )
    )
    assert [c.front for c in cards] == ["Q"]


def test_tolerates_missing_and_wrongly_typed_fields():
    # The schema constrains the model, but a schema is not a guarantee.
    cards = parse_cards(_reply([{"front": "Q", "back": "A", "tags": "not-a-list"}]))
    assert cards[0].tags == []
    assert cards[0].source_quote == ""


def test_spaces_in_tags_become_underscores():
    # Anki treats whitespace as a tag separator, so "cell biology" would silently
    # become two tags.
    card = Card.from_json({"front": "Q", "back": "A", "tags": ["cell biology"]})
    assert card.tags == ["cell_biology"]


def test_empty_card_list_is_valid():
    assert parse_cards(_reply([])) == []


def test_non_json_reply_raises():
    with pytest.raises(ResponseFormatError, match="parse"):
        parse_cards("Sorry, I can't help with that.")


def test_json_without_cards_key_raises():
    with pytest.raises(ResponseFormatError, match="cards"):
        parse_cards(json.dumps({"result": []}))


# --------------------------------------------------------------------------- #
# Response envelope handling
# --------------------------------------------------------------------------- #


def test_extracts_text_from_candidate():
    body = {"candidates": [{"content": {"parts": [{"text": "abc"}, {"text": "def"}]}}]}
    assert _extract_text(body) == "abcdef"


def test_prompt_level_block_is_reported_as_blocked():
    with pytest.raises(BlockedError, match="safety"):
        _extract_text({"promptFeedback": {"blockReason": "SAFETY"}})


def test_truncated_reply_suggests_cropping():
    body = {"candidates": [{"finishReason": "MAX_TOKENS", "content": {"parts": []}}]}
    with pytest.raises(ResponseFormatError, match="cropping"):
        _extract_text(body)


def test_no_candidates_raises():
    with pytest.raises(ResponseFormatError):
        _extract_text({"candidates": []})


# --------------------------------------------------------------------------- #
# Error mapping — the UI branches on these types, so they must be exact
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("status", [401, 403])
def test_auth_failures_map_to_auth_error(status):
    with pytest.raises(AuthError):
        _raise_for_error_payload(status, {"error": {"message": "nope"}})


def test_invalid_key_400_maps_to_auth_error():
    with pytest.raises(AuthError):
        _raise_for_error_payload(400, {"error": {"message": "API key not valid."}})


def test_429_maps_to_rate_limit_with_retry_hint():
    with pytest.raises(RateLimitError) as info:
        _raise_for_error_payload(
            429,
            {
                "error": {
                    "message": "Quota exceeded",
                    "status": "RESOURCE_EXHAUSTED",
                    "details": [
                        {"@type": "type.googleapis.com/google.rpc.RetryInfo",
                         "retryDelay": "42s"}
                    ],
                }
            },
        )
    assert info.value.retry_after == 42.0


def test_other_errors_map_to_provider_error():
    with pytest.raises(ProviderError):
        _raise_for_error_payload(500, {"error": {"message": "backend blew up"}})


# --------------------------------------------------------------------------- #
# Provider selection
# --------------------------------------------------------------------------- #


def test_missing_key_gives_actionable_config_error():
    with pytest.raises(ConfigError, match="Settings"):
        build_provider({"backend": "gemini_direct", "api_key": "", "model": "m"})


def test_missing_model_gives_actionable_config_error():
    with pytest.raises(ConfigError, match="model"):
        build_provider({"backend": "gemini_direct", "api_key": "k", "model": ""})


def test_unknown_backend_rejected():
    with pytest.raises(ConfigError, match="Unknown backend"):
        build_provider({"backend": "carrier-pigeon"})


def test_direct_provider_is_built_when_configured():
    provider = build_provider(
        {"backend": "gemini_direct", "api_key": "k", "model": "some-model"}
    )
    assert provider.model == "some-model"


def test_proxy_backend_selected_by_config():
    provider = build_provider(
        {"backend": "proxy", "proxy_url": "https://example.test/generate"}
    )
    assert provider.url.endswith("/generate")


# --------------------------------------------------------------------------- #
# Rate limiting
# --------------------------------------------------------------------------- #


def test_limiter_allows_up_to_the_limit_without_blocking():
    limiter = RateLimiter(per_minute=3)
    for _ in range(3):
        limiter.acquire()
    assert len(limiter._hits) == 3


def test_limiter_blocks_past_the_limit_and_honours_cancel():
    limiter = RateLimiter(per_minute=1)
    limiter.acquire()
    with pytest.raises(InterruptedError):
        limiter.acquire(should_cancel=lambda: True)


def test_limiter_window_expires():
    # A short window keeps the test fast while exercising the real code path.
    limiter = RateLimiter(per_minute=1, window=0.05)
    limiter.acquire()
    limiter.acquire()  # blocks ~50ms, then succeeds
    assert len(limiter._hits) == 1
