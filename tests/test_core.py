from __future__ import annotations

import json

import pytest

from core.config import coerce_config
from core.errors import (
    AuthError,
    BlockedError,
    ConfigError,
    ImageError,
    ProviderError,
    RateLimitError,
    ResponseFormatError,
)
from core.gemini import (
    _extract_retry_delay,
    _extract_text,
    _interruptible_sleep,
    _post,
    _raise_for_error_payload,
    _requests,
    choose_default_model,
    list_models,
    parse_cards,
    validate_model_id,
)
from core.imaging import (
    SUPPORTED_SUFFIXES,
    _qt,
    encode_qimage,
    load_clipboard_image,
    load_image_file,
)
from core.models import (
    Card,
    GenerationResult,
    SourceImage,
    failed_results,
    merge_results,
)
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


def test_null_fields_do_not_become_none_string():
    card = Card.from_json(
        {"front": None, "back": "A", "tags": None, "source_quote": None, "explanation": None}
    )
    assert card.front == ""
    assert card.back == "A"
    assert card.tags == []
    assert card.source_quote == ""
    assert card.explanation == ""
    assert not card.is_usable()


def test_tag_cleaning_handles_newlines_tabs_and_caps_length():
    card = Card.from_json(
        {"front": "Q", "back": "A", "tags": ["cell\t\nbiology", "a" * 150, None, 123]}
    )
    assert card.tags[0] == "cell_biology"
    assert len(card.tags[1]) == 100
    assert len(card.tags) == 2


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


def test_extract_retry_delay():
    assert _extract_retry_delay({}) is None
    assert (
        _extract_retry_delay(
            {
                "details": [
                    {
                        "@type": "type.googleapis.com/google.rpc.RetryInfo",
                        "retryDelay": "12s",
                    }
                ]
            }
        )
        == 12.0
    )


def test_requests_missing_raises_actionable_provider_error(monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "requests", None)
    with pytest.raises(ProviderError, match="pip install requests"):
        _requests()


class _DummyResponse:
    def __init__(self, status_code: int, body: dict):
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body

    @property
    def text(self):
        return json.dumps(self._body)


def test_post_retries_on_503_and_succeeds(monkeypatch):
    import requests

    responses = [
        _DummyResponse(503, {"error": {"message": "High demand", "status": "UNAVAILABLE"}}),
        _DummyResponse(200, {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}),
    ]
    calls = []

    def mock_post(*args, **kwargs):
        calls.append((args, kwargs))
        return responses.pop(0)

    monkeypatch.setattr(requests, "post", mock_post)

    slept = []
    res = _post(
        "http://example", "key", {}, max_retries=2, backoff_base=1.0, _sleep=slept.append
    )
    assert res == {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}
    assert len(calls) == 2
    assert slept == [1.0]


def test_post_exhausts_retries_on_persistent_503(monkeypatch):
    import requests

    def mock_post(*args, **kwargs):
        return _DummyResponse(
            503, {"error": {"message": "High demand", "status": "UNAVAILABLE"}}
        )

    monkeypatch.setattr(requests, "post", mock_post)

    slept = []
    with pytest.raises(ProviderError, match="503"):
        _post(
            "http://example", "key", {}, max_retries=2, backoff_base=1.0, _sleep=slept.append
        )

    assert slept == [1.0, 2.0]


def test_post_honors_retry_delay_from_server(monkeypatch):
    import requests

    responses = [
        _DummyResponse(
            503,
            {
                "error": {
                    "message": "High demand",
                    "status": "UNAVAILABLE",
                    "details": [
                        {
                            "@type": "type.googleapis.com/google.rpc.RetryInfo",
                            "retryDelay": "5s",
                        }
                    ],
                }
            },
        ),
        _DummyResponse(200, {"ok": True}),
    ]

    def mock_post(*args, **kwargs):
        return responses.pop(0)

    monkeypatch.setattr(requests, "post", mock_post)

    slept = []
    res = _post(
        "http://example", "key", {}, max_retries=2, backoff_base=1.0, _sleep=slept.append
    )
    assert res == {"ok": True}
    assert slept == [5.0]


def test_post_does_not_retry_auth_errors(monkeypatch):
    import requests

    def mock_post(*args, **kwargs):
        return _DummyResponse(
            401, {"error": {"message": "bad key", "status": "UNAUTHENTICATED"}}
        )

    monkeypatch.setattr(requests, "post", mock_post)

    slept = []
    with pytest.raises(AuthError):
        _post(
            "http://example", "key", {}, max_retries=2, backoff_base=1.0, _sleep=slept.append
        )

    assert slept == []


def test_post_rejects_non_dict_json_payload(monkeypatch):
    import requests

    def mock_post(*args, **kwargs):
        return _DummyResponse(200, ["not", "a", "dict"])

    monkeypatch.setattr(requests, "post", mock_post)

    with pytest.raises(ProviderError, match="expected JSON object"):
        _post("http://example", "key", {})


def test_list_models_rejects_non_dict_json_payload(monkeypatch):
    import requests

    def mock_get(*args, **kwargs):
        return _DummyResponse(200, ["not", "a", "dict"])

    monkeypatch.setattr(requests, "get", mock_get)

    with pytest.raises(ProviderError, match="expected JSON object"):
        list_models("key")


def test_post_caps_retry_delay_at_60s(monkeypatch):
    import requests

    responses = [
        _DummyResponse(
            503,
            {
                "error": {
                    "message": "High demand",
                    "status": "UNAVAILABLE",
                    "details": [
                        {
                            "@type": "type.googleapis.com/google.rpc.RetryInfo",
                            "retryDelay": "120s",
                        }
                    ],
                }
            },
        ),
        _DummyResponse(200, {"ok": True}),
    ]

    def mock_post(*args, **kwargs):
        return responses.pop(0)

    monkeypatch.setattr(requests, "post", mock_post)

    slept = []
    res = _post(
        "http://example", "key", {}, max_retries=2, backoff_base=1.0, _sleep=slept.append
    )
    assert res == {"ok": True}
    assert slept == [60.0]


def test_interruptible_sleep_normal():
    slept = []
    _interruptible_sleep(1.5, _sleep=slept.append)
    assert slept == [1.5]


def test_interruptible_sleep_cancels_immediately():
    slept = []
    with pytest.raises(InterruptedError, match="Cancelled while waiting"):
        _interruptible_sleep(5.0, should_cancel=lambda: True, _sleep=slept.append)
    assert slept == []


def test_interruptible_sleep_cancels_during_step():
    slept = []
    ticks = 0

    def check_cancel():
        nonlocal ticks
        ticks += 1
        return ticks > 2

    with pytest.raises(InterruptedError, match="Cancelled while waiting"):
        _interruptible_sleep(
            2.0, should_cancel=check_cancel, _sleep=slept.append, step=0.1
        )
    assert len(slept) == 2


def test_post_cancelled_while_waiting_to_retry(monkeypatch):
    import requests

    def mock_post(*args, **kwargs):
        return _DummyResponse(
            503, {"error": {"message": "High demand", "status": "UNAVAILABLE"}}
        )

    monkeypatch.setattr(requests, "post", mock_post)

    with pytest.raises(InterruptedError):
        _post(
            "http://example",
            "key",
            {},
            max_retries=2,
            should_cancel=lambda: True,
            _sleep=lambda _: None,
        )


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


def test_prefers_pinned_3_6_model_not_alphabetically_first():
    assert choose_default_model(CATALOGUE) == "gemini-3.6-flash"


def test_alphabetical_order_would_have_picked_the_broken_one():
    # Pins the bug itself: the old behaviour is what sorted() still yields.
    assert sorted(CATALOGUE)[0] == "gemini-2.0-flash"


def test_pinned_3_6_model_beats_newer_generations():
    # Newer generations (like 3.8) suffer severe free-tier capacity shedding (503)
    # and tiny daily quotas, so 3.6 is explicitly pinned as the preferred default.
    assert (
        choose_default_model([*CATALOGUE, "gemini-3.8-flash", "gemini-4.0-flash"])
        == "gemini-3.6-flash"
    )


def test_future_generation_wins_when_pinned_model_absent():
    # If the pinned model is absent or retired, the newest flash generation wins.
    assert choose_default_model(["gemini-2.0-flash", "gemini-4.0-flash"]) == "gemini-4.0-flash"


def test_minor_versions_compare_numerically():
    assert choose_default_model(["gemini-3.10-flash", "gemini-3.7-flash"]) == (
        "gemini-3.10-flash"
    )


def test_lite_is_not_preselected():
    # Cheaper, but the wrong trade for dense or handwritten pages.
    assert choose_default_model(["gemini-9.0-flash-lite", "gemini-3.7-flash"]) == (
        "gemini-3.7-flash"
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


def test_proxy_backend_rejects_plain_http():
    with pytest.raises(ConfigError, match="HTTPS"):
        build_provider(
            {"backend": "proxy", "proxy_url": "http://example.test/generate"}
        )


def test_invalid_model_id_rejected():
    with pytest.raises(ConfigError, match="Invalid model ID"):
        validate_model_id("../../secret")

    with pytest.raises(ConfigError, match="Invalid model ID"):
        validate_model_id("model?query=1")

    with pytest.raises(ConfigError, match="Invalid model ID"):
        validate_model_id("model with space")


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


def test_answer_is_html_escaped():
    out = build_back_field('<img src=x onerror="fetch(1)">')
    assert "<img" not in out
    assert "&lt;img" in out


def test_answer_with_math_inequalities_is_escaped():
    out = build_back_field("x < 3 and y > 2")
    assert "&lt; 3 and y &gt;" in out


def test_answer_preserves_quotes_without_escaping():
    out = build_back_field("Newton's \"first\" law")
    assert "Newton's \"first\" law" in out
    assert "&#x27;" not in out
    assert "&quot;" not in out


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


# --------------------------------------------------------------------------- #
# Batch result merging and retry helpers
# --------------------------------------------------------------------------- #


def test_failed_results_filters_cleanly():
    s1 = SourceImage(data=b"1", mime_type="image/jpeg", original_path="p1.jpg")
    s2 = SourceImage(data=b"2", mime_type="image/jpeg", original_path="p2.jpg")
    r1 = GenerationResult(source=s1, cards=[Card(front="Q", back="A")])
    r2 = GenerationResult(source=s2, error="RateLimitError")

    assert failed_results([r1, r2]) == [r2]
    assert failed_results([r1]) == []


def test_merge_results_replaces_matching_source_and_preserves_order():
    s1 = SourceImage(data=b"1", mime_type="image/jpeg", original_path="p1.jpg")
    s2 = SourceImage(data=b"2", mime_type="image/jpeg", original_path="p2.jpg")
    s3 = SourceImage(data=b"3", mime_type="image/jpeg", original_path="p3.jpg")

    original = [
        GenerationResult(source=s1, cards=[Card(front="Q1", back="A1")]),
        GenerationResult(source=s2, error="429 RateLimitError"),
        GenerationResult(source=s3, error="503 Service Unavailable"),
    ]

    retried = [
        GenerationResult(source=s2, cards=[Card(front="Q2", back="A2")]),
        GenerationResult(source=s3, error="503 still high demand"),
    ]

    merged = merge_results(original, retried)
    assert len(merged) == 3
    assert merged[0].source == s1 and merged[0].ok and len(merged[0].cards) == 1
    assert merged[1].source == s2 and merged[1].ok and len(merged[1].cards) == 1
    assert (
        merged[2].source == s3
        and not merged[2].ok
        and merged[2].error == "503 still high demand"
    )


def test_merge_results_appends_unmatched_new_results():
    s1 = SourceImage(data=b"1", mime_type="image/jpeg", original_path="p1.jpg")
    s2 = SourceImage(data=b"2", mime_type="image/jpeg", original_path="p2.jpg")

    original = [GenerationResult(source=s1, cards=[Card(front="Q1", back="A1")])]
    retried = [GenerationResult(source=s2, cards=[Card(front="Q2", back="A2")])]

    merged = merge_results(original, retried)
    assert len(merged) == 2
    assert merged[0].source == s1
    assert merged[1].source == s2


# --------------------------------------------------------------------------- #
# Config coercion
# --------------------------------------------------------------------------- #


def test_coerce_config_defaults_on_none_or_empty():
    conf = coerce_config(None)
    assert conf["backend"] == "gemini_direct"
    assert conf["max_image_edge"] == 1600
    assert conf["jpeg_quality"] == 85
    assert conf["requests_per_minute"] == 15
    assert conf["extra_tags"] == ["photo2cards"]


def test_coerce_config_clamps_and_normalizes_numbers():
    conf = coerce_config(
        {
            "max_image_edge": "99999",
            "jpeg_quality": "-5",
            "requests_per_minute": "not-a-number",
            "default_deck_id": "invalid",
        }
    )
    assert conf["max_image_edge"] == 4096
    assert conf["jpeg_quality"] == 10
    assert conf["requests_per_minute"] == 15
    assert conf["default_deck_id"] == 0


def test_coerce_config_handles_string_booleans():
    conf = coerce_config({"attach_source_image": "false", "attach_source_quote": "true"})
    assert conf["attach_source_image"] is False
    assert conf["attach_source_quote"] is True

    conf2 = coerce_config({"attach_source_image": "0", "attach_source_quote": "1"})
    assert conf2["attach_source_image"] is False
    assert conf2["attach_source_quote"] is True

    conf3 = coerce_config({"attach_source_image": "no", "attach_source_quote": "yes"})
    assert conf3["attach_source_image"] is False
    assert conf3["attach_source_quote"] is True


def test_coerce_config_allows_high_limits():
    conf = coerce_config({"requests_per_minute": "1000", "max_image_edge": "4096"})
    assert conf["requests_per_minute"] == 1000
    assert conf["max_image_edge"] == 4096


def test_coerce_config_handles_string_extra_tags():
    conf = coerce_config({"extra_tags": "biology, chemistry   physics"})
    assert conf["extra_tags"] == ["biology", "chemistry", "physics"]


def test_coerce_config_cleans_tags_list():
    conf = coerce_config({"extra_tags": ["tag 1", "tag\t2", None, 123]})
    assert conf["extra_tags"] == ["tag_1", "tag_2", "123"]


def test_coerce_config_unknown_backend_falls_back():
    conf = coerce_config({"backend": "unsupported_backend"})
    assert conf["backend"] == "gemini_direct"


# --------------------------------------------------------------------------- #
# Image processing and loading
# --------------------------------------------------------------------------- #


class _MockQBuffer:
    def __init__(self, byte_array):
        self._ba = byte_array

    def open(self, mode):
        pass

    def close(self):
        pass


class _MockQByteArray:
    def __init__(self):
        self.data = bytearray()

    def __bytes__(self):
        return bytes(self.data)


class _MockAspectRatioMode:
    KeepAspectRatio = 1


class _MockTransformationMode:
    SmoothTransformation = 1


class _MockOpenModeFlag:
    WriteOnly = 1


class _MockQt:
    AspectRatioMode = _MockAspectRatioMode
    TransformationMode = _MockTransformationMode


class _MockQIODevice:
    OpenModeFlag = _MockOpenModeFlag


class _MockQImage:
    def __init__(
        self,
        is_null: bool = False,
        width: int = 800,
        height: int = 600,
        has_alpha: bool = False,
        save_success: bool = True,
    ):
        self._is_null = is_null
        self._width = width
        self._height = height
        self._has_alpha = has_alpha
        self._save_success = save_success
        self.scaled_called = False

    def isNull(self):
        return self._is_null

    def width(self):
        return self._width

    def height(self):
        return self._height

    def hasAlphaChannel(self):
        return self._has_alpha

    def size(self):
        return self

    def scaled(self, w, h, aspect, mode):
        self.scaled_called = True
        return self

    def save(self, buffer, fmt, quality):
        if self._save_success:
            buffer._ba.data.extend(b"\xff\xd8\xff\xe0mock_jpeg")
            return True
        return False


class _MockQImageReader:
    def __init__(self, path: str, image: _MockQImage | None = None):
        self.path = path
        self._image = image if image is not None else _MockQImage()
        self.auto_transform = False

    def setAutoTransform(self, val: bool):
        self.auto_transform = val

    def read(self):
        return self._image


class _MockClipboard:
    def __init__(self, image: _MockQImage | None = None):
        self._image = image

    def image(self):
        return self._image


class _MockQGuiApplication:
    _clipboard: _MockClipboard | None = None

    @classmethod
    def clipboard(cls):
        return cls._clipboard


def _mock_qt_bindings(
    image: _MockQImage | None = None,
    clipboard: _MockClipboard | None = None,
    reader_instances: list[_MockQImageReader] | None = None,
):
    from core.imaging import _QtBindings

    def reader_factory(p):
        r = _MockQImageReader(p, image)
        if reader_instances is not None:
            reader_instances.append(r)
        return r

    _MockQGuiApplication._clipboard = clipboard
    return _QtBindings(
        QBuffer=_MockQBuffer,
        QByteArray=_MockQByteArray,
        QIODevice=_MockQIODevice,
        Qt=_MockQt,
        QImage=_MockQImage,
        QImageReader=reader_factory,
        QGuiApplication=_MockQGuiApplication,
    )


def test_supported_suffixes_contains_expected_extensions():
    for ext in (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"):
        assert ext in SUPPORTED_SUFFIXES


def test_qt_missing_raises_image_error():
    with pytest.raises(ImageError, match="PyQt6 is unavailable"):
        _qt()


def test_encode_qimage_rejects_null_image(monkeypatch):
    monkeypatch.setattr("core.imaging._qt", _mock_qt_bindings)
    with pytest.raises(ImageError, match="The image could not be decoded"):
        encode_qimage(_MockQImage(is_null=True))


def test_encode_qimage_downscales_when_exceeding_max_edge(monkeypatch):
    monkeypatch.setattr("core.imaging._qt", _mock_qt_bindings)
    img = _MockQImage(width=3000, height=2000)
    data, mime = encode_qimage(img, max_edge=1600)
    assert img.scaled_called is True
    assert mime == "image/jpeg"
    assert data == b"\xff\xd8\xff\xe0mock_jpeg"


def test_encode_qimage_raises_on_save_failure(monkeypatch):
    monkeypatch.setattr("core.imaging._qt", _mock_qt_bindings)
    img = _MockQImage(save_success=False)
    with pytest.raises(ImageError, match="Failed to encode the image as JPEG"):
        encode_qimage(img)


def test_load_image_file_rejects_unreadable_file(monkeypatch):
    monkeypatch.setattr(
        "core.imaging._qt",
        lambda: _mock_qt_bindings(image=_MockQImage(is_null=True)),
    )
    with pytest.raises(ImageError, match="Could not read"):
        load_image_file("photo.jpg")


def test_load_image_file_reads_and_encodes(monkeypatch):
    readers: list[_MockQImageReader] = []
    monkeypatch.setattr(
        "core.imaging._qt",
        lambda: _mock_qt_bindings(
            image=_MockQImage(width=800, height=600), reader_instances=readers
        ),
    )
    data, mime = load_image_file("photo.jpg")
    assert mime == "image/jpeg"
    assert data == b"\xff\xd8\xff\xe0mock_jpeg"
    assert len(readers) == 1
    assert readers[0].auto_transform is True


def test_load_clipboard_image_no_clipboard(monkeypatch):
    monkeypatch.setattr("core.imaging._qt", lambda: _mock_qt_bindings(clipboard=None))
    with pytest.raises(ImageError, match="No clipboard is available"):
        load_clipboard_image()


def test_load_clipboard_image_null_image(monkeypatch):
    monkeypatch.setattr(
        "core.imaging._qt",
        lambda: _mock_qt_bindings(clipboard=_MockClipboard(image=_MockQImage(is_null=True))),
    )
    with pytest.raises(ImageError, match="There is no image on the clipboard"):
        load_clipboard_image()


def test_load_clipboard_image_success(monkeypatch):
    monkeypatch.setattr(
        "core.imaging._qt",
        lambda: _mock_qt_bindings(
            clipboard=_MockClipboard(image=_MockQImage(width=800, height=600))
        ),
    )
    data, mime = load_clipboard_image()
    assert mime == "image/jpeg"
    assert data == b"\xff\xd8\xff\xe0mock_jpeg"

