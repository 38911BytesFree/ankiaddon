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
from core.gemini import (
    _extract_text,
    _raise_for_error_payload,
    choose_default_model,
    parse_cards,
)
from core.models import Card
from core.provider import build_provider
from core.ratelimit import RateLimiter
from core.render import answer_of, build_back_field, split_back_field

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


def test_parses_card_with_explanation():
    cards = parse_cards(
        _reply(
            [
                {
                    "front": "Why do arteries have thicker walls than veins?",
                    "back": "To withstand high blood pressure.",
                    "explanation": "Blood pumped from ventricles is under high pressure.",
                    "tags": ["circulatory_system"],
                    "source_quote": "Arteries have thick walls to withstand pressure.",
                }
            ]
        )
    )
    assert len(cards) == 1
    assert cards[0].explanation.startswith("Blood pumped")


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
# Default model choice
#
# Regression cover for a real failure: the dropdown was sorted alphabetically and
# the default was the first id containing "flash", so users were preselected onto
# gemini-2.0-flash, whose free-tier allocation is retired. First request 429'd.
# --------------------------------------------------------------------------- #


# A realistic ListModels result, deliberately not in preference order.
CATALOGUE = [
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
    "gemini-3.6-flash",
    "gemini-3.6-pro",
    "gemini-flash-latest",
]


def test_prefers_newest_flash_not_alphabetically_first():
    assert choose_default_model(CATALOGUE) == "gemini-3.6-flash"


def test_alphabetical_order_would_have_picked_the_broken_one():
    # Pins the bug itself: the old behaviour is what sorted() still yields.
    assert sorted(CATALOGUE)[0] == "gemini-2.0-flash"


def test_a_future_generation_wins_automatically():
    # The fix must not be pinned to 3.6, or it rots into the same bug.
    assert choose_default_model([*CATALOGUE, "gemini-4.0-flash"]) == "gemini-4.0-flash"


def test_minor_versions_compare_numerically():
    assert choose_default_model(["gemini-3.10-flash", "gemini-3.6-flash"]) == (
        "gemini-3.10-flash"
    )


def test_lite_is_not_preselected():
    # Cheaper, but the wrong trade for dense or handwritten pages.
    assert choose_default_model(["gemini-9.0-flash-lite", "gemini-3.6-flash"]) == (
        "gemini-3.6-flash"
    )


def test_base_model_beats_a_longer_variant_of_the_same_version():
    assert choose_default_model(
        ["gemini-3.6-flash-native-audio", "gemini-3.6-flash"]
    ) == "gemini-3.6-flash"


def test_falls_back_to_lite_when_that_is_the_only_flash():
    assert choose_default_model(["gemini-3.6-flash-lite"]) == "gemini-3.6-flash-lite"


def test_falls_back_to_any_model_when_no_flash_exists():
    assert choose_default_model(["gemini-3.6-pro"]) == "gemini-3.6-pro"


def test_unversioned_ids_rank_below_versioned_ones():
    assert choose_default_model(["gemini-flash-latest", "gemini-2.0-flash"]) == (
        "gemini-2.0-flash"
    )


def test_empty_catalogue_returns_none():
    assert choose_default_model([]) is None


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
# Back-field composition
# --------------------------------------------------------------------------- #


def test_answer_only_is_left_alone():
    assert build_back_field("The mitochondrion") == "The mitochondrion"


def test_quote_is_appended_below_the_answer():
    out = build_back_field("The mitochondrion", quote="It is the powerhouse.")
    assert out.startswith("The mitochondrion")
    assert "It is the powerhouse." in out
    assert out.index("mitochondrion") < out.index("powerhouse")


def test_quote_is_html_escaped():
    # Maths and chemistry sources routinely contain < and &; unescaped, Anki
    # swallows the rest of the field.
    out = build_back_field("x is smaller", quote="a < b & c > d")
    assert "&lt; b &amp; c &gt;" in out
    assert "a < b &" not in out


def test_blank_quote_adds_nothing():
    assert build_back_field("answer", quote="   ") == "answer"


def test_image_is_width_constrained():
    # Without this a phone photo renders at full pixel size and dwarfs the answer.
    out = build_back_field("answer", image_filename="page.jpg")
    assert 'src="page.jpg"' in out
    assert "max-width:100%" in out


def test_image_is_wrapped_in_collapsible_details():
    out = build_back_field("answer", image_filename="page.jpg")
    assert "<details" in out
    assert "<summary" in out
    assert "Source photo" in out
    assert "</details>" in out


def test_image_filename_is_attribute_escaped():
    out = build_back_field("answer", image_filename='odd" name.jpg')
    assert 'src="odd&quot; name.jpg"' in out


def test_quote_precedes_image_when_both_present():
    out = build_back_field("answer", quote="cited text", image_filename="page.jpg")
    assert out.index("cited text") < out.index("page.jpg")


def test_quote_and_image_are_independent():
    quote_only = build_back_field("a", quote="q")
    image_only = build_back_field("a", image_filename="p.jpg")
    assert "img" not in quote_only
    assert "q" not in image_only.replace("a", "")


def test_explanation_is_appended_below_answer():
    out = build_back_field(
        "Pulmonary veins",
        explanation="They are the only veins carrying oxygenated blood.",
    )
    assert out.startswith("Pulmonary veins")
    assert "<em>Explanation:</em> They are the only veins" in out


def test_explanation_is_html_escaped():
    out = build_back_field("answer", explanation="a < b & c > d")
    assert "&lt; b &amp; c &gt;" in out


def test_blank_explanation_adds_nothing():
    assert build_back_field("answer", explanation="   ") == "answer"


def test_explanation_precedes_quote_and_image():
    out = build_back_field(
        "answer",
        explanation="because why",
        quote="cited text",
        image_filename="page.jpg",
    )
    assert out.index("answer") < out.index("because why")
    assert out.index("because why") < out.index("cited text")
    assert out.index("cited text") < out.index("page.jpg")


# --------------------------------------------------------------------------- #
# Back-field decomposition
#
# Comparing a generated card against one already in the deck means comparing
# answers, so the citation and photo have to come back off first.
# --------------------------------------------------------------------------- #


def test_split_round_trips_a_bare_answer():
    assert split_back_field(build_back_field("The liver")) == ("The liver", "")


def test_split_round_trips_an_explanation():
    back = build_back_field(
        "The liver",
        explanation="It performs metabolic detoxification.",
    )
    assert split_back_field(back) == ("The liver", "")


def test_split_round_trips_a_quote():
    back = build_back_field("The liver", quote="the liver filters blood")
    assert split_back_field(back) == ("The liver", "the liver filters blood")


def test_split_round_trips_an_image():
    back = build_back_field("The liver", image_filename="page.jpg")
    assert split_back_field(back) == ("The liver", "")


def test_split_round_trips_both():
    back = build_back_field("The liver", quote="cited", image_filename="page.jpg")
    assert split_back_field(back) == ("The liver", "cited")


def test_split_round_trips_all_decorations():
    back = build_back_field(
        "The liver",
        explanation="It performs metabolic detoxification.",
        quote="cited",
        image_filename="page.jpg",
    )
    assert split_back_field(back) == ("The liver", "cited")


def test_split_strips_legacy_div_image():
    legacy = (
        'The liver<div style="margin-top:0.9em">'
        '<img src="page.jpg" style="max-width:100%;height:auto"></div>'
    )
    assert split_back_field(legacy) == ("The liver", "")

    legacy_with_quote = (
        'The liver<div style="margin-top:0.9em;padding-top:0.6em;'
        'border-top:1px solid rgba(128,128,128,0.35);font-size:0.85em;'
        'opacity:0.75;text-align:left">&ldquo;cited&rdquo;</div>'
        '<div style="margin-top:0.9em">'
        '<img src="page.jpg" style="max-width:100%;height:auto"></div>'
    )
    assert split_back_field(legacy_with_quote) == ("The liver", "cited")


def test_split_unescapes_the_quote_it_recovers():
    back = build_back_field("x is smaller", quote="a < b & c > d")
    assert split_back_field(back)[1] == "a < b & c > d"


def test_split_keeps_markup_inside_the_answer():
    back = build_back_field("<b>The liver</b>", quote="cited")
    assert split_back_field(back)[0] == "<b>The liver</b>"


def test_split_leaves_a_hand_written_back_whole():
    # Not something this add-on wrote, so there is nothing to strip. Returning it
    # intact makes it compare as different, which sends it to the user for a
    # decision rather than guessing at its structure.
    hand_written = "<div>Something a person typed</div>"
    assert split_back_field(hand_written) == (hand_written, "")


def test_split_does_not_mistake_the_quote_div_for_the_image_div():
    # The two wrappers share a margin-top prefix; only an exact style match may
    # count as the image.
    back = build_back_field("answer", quote="cited")
    assert split_back_field(back) == ("answer", "cited")


def test_split_tolerates_an_empty_back():
    assert split_back_field("") == ("", "")


def test_answer_of_is_the_first_half_of_split():
    back = build_back_field("The liver", quote="cited", image_filename="page.jpg")
    assert answer_of(back) == split_back_field(back)[0] == "The liver"


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
