"""Tests for the "is this the same card" rules.

The comparison layer is the one place where being slightly wrong is expensive in
both directions: too strict and the add-on re-adds cards the user already has,
too loose and it silently swallows a card that was genuinely different.
"""

from core.dedupe import collapse_identical, group_by_front, identity, normalize
from core.models import Card, SourceImage


def card(front, back, tags=None, quote=""):
    return Card(front=front, back=back, tags=tags or [], source_quote=quote)


def image(name):
    return SourceImage(data=b"x", mime_type="image/jpeg", original_path=name)


# --- normalize ---------------------------------------------------------- #


def test_normalize_strips_markup():
    assert normalize("<b>The liver</b>") == "the liver"


def test_normalize_unescapes_entities():
    assert normalize("Tom &amp; Jerry") == "tom & jerry"


def test_normalize_collapses_whitespace_and_breaks():
    assert normalize("one<br>two   three\n four") == "one two three four"


def test_normalize_treats_nbsp_as_a_space():
    # Anki's editor writes &nbsp; where the model wrote a plain space; that is
    # not a different answer.
    assert normalize("a&nbsp;b") == normalize("a b")


def test_normalize_ignores_case():
    assert normalize("Osmosis") == normalize("osmosis")


def test_normalize_handles_empty_and_none_ish():
    assert normalize("") == ""
    assert normalize(None) == ""


def test_normalize_does_not_merge_different_content():
    assert normalize("the liver") != normalize("the kidney")


def test_identity_is_front_and_back():
    assert identity(card("<i>Q</i>", "A", quote="anything")) == ("q", "a")


def test_identity_ignores_the_source_quote():
    # The whole point: a re-shot page carries a different quote and is still the
    # same card.
    assert identity(card("Q", "A", quote="page 1")) == identity(
        card("Q", "A", quote="page 2")
    )


# --- collapse_identical -------------------------------------------------- #


def test_collapse_drops_an_exact_repeat():
    pairs = [(card("Q", "A"), None), (card("Q", "A"), None)]
    assert len(collapse_identical(pairs)) == 1


def test_collapse_keeps_cards_that_differ_in_the_back():
    pairs = [(card("Q", "A"), None), (card("Q", "B"), None)]
    assert len(collapse_identical(pairs)) == 2


def test_collapse_keeps_the_later_quote():
    first, second = image("one.jpg"), image("two.jpg")
    pairs = [
        (card("Q", "A", quote="older"), first),
        (card("Q", "A", quote="newer"), second),
    ]
    (kept, source), = collapse_identical(pairs)
    assert kept.source_quote == "newer"
    # The photo follows the quote, so provenance stays internally consistent.
    assert source is second


def test_collapse_keeps_the_earlier_quote_when_the_later_has_none():
    first, second = image("one.jpg"), image("two.jpg")
    pairs = [
        (card("Q", "A", quote="the only quote"), first),
        (card("Q", "A", quote=""), second),
    ]
    (kept, source), = collapse_identical(pairs)
    assert kept.source_quote == "the only quote"
    assert source is first


def test_collapse_merges_tags_from_the_dropped_copy():
    pairs = [(card("Q", "A", tags=["bio"]), None), (card("Q", "A", tags=["exam"]), None)]
    (kept, _), = collapse_identical(pairs)
    assert kept.tags == ["bio", "exam"]


def test_collapse_matches_across_formatting_differences():
    pairs = [(card("Q", "<b>A</b>"), None), (card("q", "A"), None)]
    assert len(collapse_identical(pairs)) == 1


def test_collapse_preserves_order_of_first_appearance():
    pairs = [
        (card("first", "A"), None),
        (card("second", "B"), None),
        (card("first", "A"), None),
    ]
    fronts = [c.front for c, _ in collapse_identical(pairs)]
    assert fronts == ["first", "second"]


def test_collapse_of_nothing_is_nothing():
    assert collapse_identical([]) == []


# --- group_by_front ------------------------------------------------------ #


def test_group_moves_shared_fronts_together():
    pairs = [
        (card("same", "A"), None),
        (card("other", "B"), None),
        (card("same", "C"), None),
    ]
    ordered, groups = group_by_front(pairs)
    assert [c.back for c, _ in ordered] == ["A", "C", "B"]
    assert groups == [0, 0, -1]


def test_group_marks_a_unique_front_with_minus_one():
    _, groups = group_by_front([(card("only", "A"), None)])
    assert groups == [-1]


def test_group_numbers_separate_clashes_separately():
    pairs = [
        (card("a", "1"), None),
        (card("b", "1"), None),
        (card("a", "2"), None),
        (card("b", "2"), None),
    ]
    _, groups = group_by_front(pairs)
    assert groups == [0, 0, 1, 1]


def test_group_ignores_formatting_when_matching_fronts():
    pairs = [(card("<b>Q</b>", "A"), None), (card("q", "B"), None)]
    _, groups = group_by_front(pairs)
    assert groups == [0, 0]


def test_group_returns_a_parallel_list():
    pairs = [(card(f"q{i}", "A") if i else card("q", "A"), None) for i in range(4)]
    ordered, groups = group_by_front(pairs)
    assert len(ordered) == len(groups) == 4
