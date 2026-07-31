"""Deciding when two cards are the same card.

A card's identity is its front and its back, and nothing else. The source quote
and the attached photo are provenance, not content: the same question and answer
read off two photos of the same page is one card, and the newer read of the page
is the one worth keeping.

Pure logic, so `tests/` can cover the comparison rules without launching Anki.
"""

from __future__ import annotations

import html
import re
from dataclasses import replace
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - import exists for annotations only
    from .models import Card, SourceImage

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")

#: Spelled with chr() rather than the character itself, which would sit in the
#: source as an invisible literal. Anki's editor emits these freely where the
#: model wrote a plain space.
_NBSP = chr(0xA0)


def normalize(text: str) -> str:
    """Reduce a field to what a reader would call its content.

    Markup, entities, non-breaking spaces and capitalisation all vary between a
    field the model produced and the same field after a round trip through
    Anki's editor, and none of those differences make it a different card.
    """
    stripped = _TAG_RE.sub(" ", text or "")
    # `<br>` became a space above, so unescaping now cannot resurrect a tag.
    unescaped = html.unescape(stripped).replace(_NBSP, " ")
    return _WS_RE.sub(" ", unescaped).strip().casefold()


def identity(card: Card) -> tuple[str, str]:
    """The pair that decides whether two cards are the same card."""
    return normalize(card.front), normalize(card.back)


def collapse_identical(
    pairs: list[tuple[Card, SourceImage | None]],
) -> list[tuple[Card, SourceImage | None]]:
    """Drop repeats of the same front-and-back, keeping the freshest provenance.

    Photographing overlapping pages is the normal way to use this add-on, so the
    same card arriving twice is expected rather than exceptional and is not worth
    showing the user. Order is preserved: the first appearance keeps its place in
    the list, but picks up the later appearance's quote and photo, since a later
    shot of the same page is usually the better read of it. Tags are merged, so
    nothing a dropped copy carried is lost.
    """
    order: list[tuple[str, str]] = []
    kept: dict[tuple[str, str], tuple[Card, SourceImage | None]] = {}

    for card, source in pairs:
        key = identity(card)
        if key not in kept:
            order.append(key)
            kept[key] = (card, source)
            continue

        winner, winner_source = kept[key]
        if card.source_quote.strip() and card.source_quote != winner.source_quote:
            winner = replace(winner, source_quote=card.source_quote)
            winner_source = source
        merged = list(winner.tags) + [t for t in card.tags if t not in winner.tags]
        kept[key] = (replace(winner, tags=merged), winner_source)

    return [kept[key] for key in order]


def group_by_front(
    pairs: list[tuple[Card, SourceImage | None]],
) -> tuple[list[tuple[Card, SourceImage | None]], list[int]]:
    """Move cards that share a front next to each other.

    Same front with a different back is a choice the user has to make, and they
    can only make it if the alternatives are visible together. Returns the
    reordered pairs and, parallel to them, a group id per row: rows sharing an id
    share a front, and `-1` means the front appears once.
    """
    buckets: dict[str, list[tuple[Card, SourceImage | None]]] = {}
    order: list[str] = []

    for pair in pairs:
        key = normalize(pair[0].front)
        if key not in buckets:
            buckets[key] = []
            order.append(key)
        buckets[key].append(pair)

    ordered: list[tuple[Card, SourceImage | None]] = []
    group_ids: list[int] = []
    next_id = 0

    for key in order:
        bucket = buckets[key]
        if len(bucket) > 1:
            group_id = next_id
            next_id += 1
        else:
            group_id = -1
        for pair in bucket:
            ordered.append(pair)
            group_ids.append(group_id)

    return ordered, group_ids
