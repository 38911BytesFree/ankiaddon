"""The page view: transcripts, summaries, underlines, and single-card requests."""

from __future__ import annotations

import json

import pytest

from core import gemini
from core.anchors import (
    EXACT_COLOR,
    QUOTE_COLOR,
    Anchor,
    card_href,
    card_id_from_href,
    find_anchor,
    locate,
    render_page,
)
from core.config import coerce_config
from core.errors import BlockedError, RecitationError, ResponseFormatError
from core.gemini import (
    GeminiProvider,
    _build_card_payload,
    _build_payload,
    _extract_text,
    parse_page,
    parse_single_card,
)
from core.models import SourceImage
from core.prompts import CARD_SCHEMA, response_schema, system_prompt
from core.provider import ProxyProvider
from core.render import build_back_field, split_back_field

PAGE = (
    "Cell Biology\n"
    "The mitochondrion is the site of aerobic respiration.\n"
    "Plants make glucose by photosynthesis, using light energy.\n"
    "Water boils at 100 °C at sea level."
)

_IMAGE = SourceImage(data=b"\xff\xd8", mime_type="image/jpeg")


# --- locate --------------------------------------------------------------------


def test_locate_ignores_case_and_line_breaks():
    span = locate(PAGE, "the MITOCHONDRION   is\nthe site")
    assert span is not None
    assert PAGE[span[0] : span[1]] == "The mitochondrion is the site"


def test_locate_treats_curly_and_straight_quotes_alike():
    text = "He said “osmosis” isn’t active."
    span = locate(text, "\"osmosis\" isn't")
    assert span is not None
    assert text[span[0] : span[1]] == "“osmosis” isn’t"


def test_locate_treats_dash_variants_alike():
    text = "pages 10–12"
    assert locate(text, "10-12") is not None


def test_locate_matches_whole_words_only():
    assert locate("the cat sat", "a") is None
    assert locate("mitochondrion", "ion") is None


def test_locate_picks_the_occurrence_nearest_to_a_hint():
    text = "DNA here. And DNA there."
    assert locate(text, "DNA") == (0, 3)
    assert locate(text, "DNA", near=15) == (14, 17)


def test_locate_empty_needle_is_none():
    assert locate(PAGE, "   ") is None


# --- find_anchor -----------------------------------------------------------------


def test_answer_inside_quote_is_underlined_exactly():
    anchor = find_anchor(
        PAGE,
        "aerobic respiration",
        "The mitochondrion is the site of aerobic respiration.",
    )
    assert anchor is not None and anchor.exact
    assert PAGE[anchor.start : anchor.end] == "aerobic respiration"


def test_answer_trailing_full_stop_is_ignored():
    anchor = find_anchor(PAGE, "Photosynthesis.", "Plants make glucose by photosynthesis")
    assert anchor is not None and anchor.exact
    assert PAGE[anchor.start : anchor.end] == "photosynthesis"


def test_paraphrased_answer_falls_back_to_the_quote():
    quote = "Plants make glucose by photosynthesis, using light energy."
    anchor = find_anchor(PAGE, "They convert light into chemical energy", quote)
    assert anchor is not None and not anchor.exact
    assert PAGE[anchor.start : anchor.end] == quote


def test_short_answer_is_found_inside_its_quote_not_elsewhere():
    text = "Group 1 has 1 valence electron.\nNoble gases have 8 valence electrons."
    anchor = find_anchor(text, "8", "Noble gases have 8 valence electrons.")
    assert anchor is not None and anchor.exact
    assert anchor.start == text.index("8")


def test_answer_found_elsewhere_in_page_is_not_used_when_quote_is_found():
    # The quote is the card's grounding; an identical word in another sentence
    # is not evidence about this card.
    anchor = find_anchor(PAGE, "glucose", "The mitochondrion is the site")
    assert anchor is not None and not anchor.exact
    assert PAGE[anchor.start : anchor.end] == "The mitochondrion is the site"


def test_unique_answer_is_used_when_quote_is_missing():
    anchor = find_anchor(PAGE, "photosynthesis", "a quote the model invented")
    assert anchor is not None and anchor.exact
    assert PAGE[anchor.start : anchor.end] == "photosynthesis"


def test_ambiguous_answer_without_quote_is_not_guessed():
    text = "DNA is copied. DNA is read."
    assert find_anchor(text, "DNA", "") is None


def test_ambiguous_answer_is_resolved_by_near():
    text = "DNA is copied. DNA is read."
    anchor = find_anchor(text, "DNA", "", near=14)
    assert anchor == Anchor(15, 18, exact=True)


def test_quote_with_ellipsis_spans_both_ends():
    quote = "The mitochondrion ... aerobic respiration"
    anchor = find_anchor(PAGE, "not on page", quote)
    assert anchor is not None and not anchor.exact
    assert PAGE[anchor.start : anchor.end] == (
        "The mitochondrion is the site of aerobic respiration"
    )


def test_nothing_on_page_gives_no_anchor():
    assert find_anchor(PAGE, "Krebs cycle", "the Krebs cycle runs in the matrix") is None


def test_empty_transcript_gives_no_anchor():
    assert find_anchor("", "x", "x") is None


# --- render_page ---------------------------------------------------------------


def test_render_without_anchors_is_escaped_plain_text():
    out = render_page("a < b & c\nnext", {})
    assert "a &lt; b &amp; c<br>next" in out
    assert "<a " not in out


def test_render_underlines_each_card_as_a_link():
    start = PAGE.index("photosynthesis")
    out = render_page(PAGE, {4: Anchor(start, start + 14, exact=True)})
    assert f'href="{card_href(4)}"' in out
    assert ">photosynthesis</a>" in out
    assert EXACT_COLOR in out


def test_render_colours_quote_anchors_differently():
    out = render_page("abc def", {0: Anchor(0, 3, exact=False)})
    assert QUOTE_COLOR in out
    assert EXACT_COLOR not in out


def test_render_nested_spans_link_the_inner_text_to_the_narrower_card():
    text = "one two three"
    out = render_page(text, {0: Anchor(0, 13, exact=False), 1: Anchor(4, 7, exact=True)})
    assert f'<a href="{card_href(1)}"' in out and ">two</a>" in out
    assert f'href="{card_href(0)}"' in out and ">one </a>" in out


def test_render_highlights_the_active_card():
    out = render_page("abc def", {2: Anchor(4, 7, exact=True)}, active=2, active_bg="#123456")
    assert "background-color:#123456" in out
    out = render_page("abc def", {2: Anchor(4, 7, exact=True)}, active=9, active_bg="#123456")
    assert "#123456" not in out


def test_render_preserves_whitespace_so_offsets_line_up():
    out = render_page("a  b", {})
    assert "white-space:pre-wrap" in out
    assert "a  b" in out


def test_card_href_round_trips():
    assert card_id_from_href(card_href(17)) == 17
    assert card_id_from_href("https://example.com") is None
    assert card_id_from_href("card:abc") is None


# --- parsing and prompts ---------------------------------------------------------


def _page_reply(**extra) -> str:
    body = {"cards": [{"front": "Q", "back": "A", "tags": [], "source_quote": "A"}]}
    body.update(extra)
    return json.dumps(body)


def test_parse_page_reads_transcript_and_summary():
    reading = parse_page(_page_reply(transcript="  Line 1\nLine 2 ", summary=" About X. "))
    assert reading.transcript == "Line 1\nLine 2"
    assert reading.summary == "About X."
    assert [c.front for c in reading.cards] == ["Q"]


def test_parse_page_tolerates_missing_or_wrong_typed_extras():
    reading = parse_page(_page_reply(transcript=None, summary=["no"]))
    assert reading.transcript == ""
    assert reading.summary == ""
    assert len(reading.cards) == 1


def test_parse_page_still_requires_cards():
    with pytest.raises(ResponseFormatError):
        parse_page(json.dumps({"transcript": "x", "summary": "y"}))


def test_schema_orders_transcript_before_cards():
    schema = response_schema()
    assert schema["propertyOrdering"] == ["transcript", "summary", "cards"]
    assert schema["required"] == ["transcript", "summary", "cards"]


def test_schema_without_transcript_keeps_summary_and_cards():
    schema = response_schema(transcribe=False)
    assert "transcript" not in schema["properties"]
    assert schema["propertyOrdering"] == ["summary", "cards"]


def test_prompt_mentions_transcript_only_when_asked():
    assert "## transcript" in system_prompt()
    assert "## transcript" not in system_prompt(transcribe=False)
    assert "## summary" in system_prompt(transcribe=False)


def test_payload_without_transcript_uses_matching_prompt_and_schema():
    payload = _build_payload(_IMAGE, "", transcribe=False)
    assert payload["systemInstruction"]["parts"][0]["text"] == system_prompt(transcribe=False)
    assert "transcript" not in payload["generationConfig"]["responseSchema"]["properties"]


def test_card_payload_is_text_only_and_carries_page_and_passage():
    payload = _build_card_payload("PAGE TEXT", "the passage", "Biology")
    parts = payload["contents"][0]["parts"]
    assert len(parts) == 1 and "inline_data" not in parts[0]
    assert "PAGE TEXT" in parts[0]["text"]
    assert "the passage" in parts[0]["text"]
    assert "Biology" in parts[0]["text"]
    assert payload["generationConfig"]["responseSchema"] is CARD_SCHEMA


def test_single_card_takes_the_passage_as_its_quote():
    card = parse_single_card(
        json.dumps({"front": "Q?", "back": "A", "tags": ["t"], "source_quote": "made up"}),
        " the passage ",
    )
    assert (card.front, card.back, card.source_quote) == ("Q?", "A", "the passage")


def test_single_card_needs_question_and_answer():
    with pytest.raises(ResponseFormatError):
        parse_single_card(json.dumps({"front": "Q?", "back": ""}), "p")


def test_recitation_is_its_own_blocked_error():
    body = {"candidates": [{"finishReason": "RECITATION"}]}
    with pytest.raises(RecitationError) as info:
        _extract_text(body)
    assert isinstance(info.value, BlockedError)


# --- provider ------------------------------------------------------------------


def _ok(text: str) -> dict:
    return {"candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": "STOP"}]}


def test_read_page_returns_transcript_and_summary(monkeypatch):
    calls = []

    def fake_post(url, key, payload, **kwargs):
        calls.append(payload)
        return _ok(_page_reply(transcript="T", summary="S"))

    monkeypatch.setattr(gemini, "_post", fake_post)
    reading = GeminiProvider("k", "gemini-x").read_page(_IMAGE)
    assert (reading.transcript, reading.summary, len(reading.cards)) == ("T", "S", 1)
    assert len(calls) == 1


def test_read_page_retries_without_transcript_on_recitation(monkeypatch):
    calls = []

    def fake_post(url, key, payload, **kwargs):
        calls.append(payload)
        if len(calls) == 1:
            return {"candidates": [{"finishReason": "RECITATION"}]}
        # A model that ignores the schema and still sends a transcript must not
        # get it shown: this request asked for none.
        return _ok(_page_reply(transcript="leaked", summary="S"))

    monkeypatch.setattr(gemini, "_post", fake_post)
    reading = GeminiProvider("k", "gemini-x").read_page(_IMAGE)
    assert len(calls) == 2
    assert "transcript" in calls[0]["generationConfig"]["responseSchema"]["properties"]
    assert "transcript" not in calls[1]["generationConfig"]["responseSchema"]["properties"]
    assert reading.transcript == ""
    assert reading.summary == "S"
    assert len(reading.cards) == 1


def test_read_page_does_not_retry_after_cancel(monkeypatch):
    def fake_post(url, key, payload, **kwargs):
        return {"candidates": [{"finishReason": "RECITATION"}]}

    monkeypatch.setattr(gemini, "_post", fake_post)
    with pytest.raises(InterruptedError):
        GeminiProvider("k", "gemini-x").read_page(_IMAGE, should_cancel=lambda: True)


def test_generate_cards_still_returns_cards(monkeypatch):
    monkeypatch.setattr(gemini, "_post", lambda *a, **k: _ok(_page_reply()))
    cards = GeminiProvider("k", "gemini-x").generate_cards(_IMAGE)
    assert [c.front for c in cards] == ["Q"]


def test_write_card_sends_one_text_request(monkeypatch):
    seen = {}

    def fake_post(url, key, payload, **kwargs):
        seen["payload"] = payload
        return _ok(json.dumps({"front": "What makes glucose?", "back": "photosynthesis"}))

    monkeypatch.setattr(gemini, "_post", fake_post)
    card = GeminiProvider("k", "gemini-x").write_card(PAGE, "photosynthesis")
    assert card.front == "What makes glucose?"
    assert card.source_quote == "photosynthesis"
    assert "photosynthesis" in seen["payload"]["contents"][0]["parts"][0]["text"]


def test_proxy_cannot_write_a_card():
    from core.errors import ProviderError

    with pytest.raises(ProviderError):
        ProxyProvider("https://x", "t").write_card(PAGE, "p")


# --- the card back --------------------------------------------------------------


def test_summary_is_a_collapsible_section():
    back = build_back_field("A", summary="About cells.")
    assert "<details" in back and ">Page summary</summary>" in back
    assert "About cells." in back


def test_summary_is_escaped_and_keeps_line_breaks():
    back = build_back_field("A", summary="x < y\nnext")
    assert "x &lt; y<br>next" in back


def test_blank_summary_adds_nothing():
    assert build_back_field("A", summary="  ") == "A"


def test_summary_sits_between_quote_and_photo():
    back = build_back_field("A", quote="Q", image_filename="p.jpg", summary="S")
    assert back.index("&ldquo;Q") < back.index("Page summary") < back.index("Source photo")


def test_split_strips_summary():
    back = build_back_field("A", quote="Q", summary="S", explanation="E")
    assert split_back_field(back) == ("A", "Q")


def test_split_strips_summary_and_photo_together():
    back = build_back_field("A", quote="Q", image_filename="p.jpg", summary="S\nT")
    assert split_back_field(back) == ("A", "Q")


def test_config_attach_page_summary_defaults_on_and_coerces():
    assert coerce_config({})["attach_page_summary"] is True
    assert coerce_config({"attach_page_summary": "no"})["attach_page_summary"] is False
