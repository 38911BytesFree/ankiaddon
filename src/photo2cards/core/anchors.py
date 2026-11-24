"""Finding a card on the page, and drawing the page with its cards underlined.

The review dialog shows each photo's transcript with every card marked where it
came from. A card is underlined at its answer when the answer appears on the
page word for word, and at its source quote otherwise — the quote is copied from
the page, so it is the anchor that is always meant to be there.

Matching is deliberately forgiving about the things a transcription and a card
legitimately disagree on — line breaks, runs of spaces, capitalisation, curly
versus straight quotes, dash variants — and strict about everything else. A
missing underline is a visible, harmless outcome; an underline under the wrong
words would teach the user to distrust all of them.

Pure logic and HTML, so `tests/` covers it without launching Anki.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass

#: Characters a model and a transcription may use interchangeably.
_EQUIVALENT = {
    **{c: "['‘’‛`´]" for c in "'‘’‛`´"},
    **{c: '["“”„‟]' for c in '"“”„‟'},
    **{c: "[-‐‑‒–—―−]" for c in "-‐‑‒–—―−"},
}

#: An answer ending in sentence punctuation still counts as found when the page
#: carries the words without it, as in a list item or a table cell.
_TRAILING_PUNCT = ".,;:!?"

#: A quote the model shortened with an ellipsis is found by its two ends, as long
#: as they are close enough together to be the same passage.
_ELLIPSIS_RE = re.compile(r"\s*(?:\.\.\.|…)\s*")
_MAX_ELIDED_GAP = 600


@dataclass(frozen=True)
class Anchor:
    """Where a card sits in a transcript: `transcript[start:end]`."""

    start: int
    end: int
    #: True when this is the answer itself, False when it is the source quote.
    exact: bool


def _pattern(needle: str) -> re.Pattern | None:
    needle = needle.strip()
    if not needle:
        return None

    parts: list[str] = []
    in_space = False
    for ch in needle:
        if ch.isspace():
            if not in_space:
                parts.append(r"\s+")
            in_space = True
            continue
        in_space = False
        parts.append(_EQUIVALENT.get(ch, re.escape(ch)))

    # Whole words only at a word edge, so the answer "a" is not found inside
    # "cat" and "ion" is not found inside "mitochondrion".
    head = r"(?<!\w)" if re.match(r"\w", needle[0]) else ""
    tail = r"(?!\w)" if re.match(r"\w", needle[-1]) else ""
    return re.compile(head + "".join(parts) + tail, re.IGNORECASE)


def _matches(
    haystack: str, needle: str, start: int = 0, end: int | None = None
) -> list[tuple[int, int]]:
    pattern = _pattern(needle)
    if pattern is None:
        return []
    stop = len(haystack) if end is None else end
    return [m.span() for m in pattern.finditer(haystack, start, stop)]


def _nearest(spans: list[tuple[int, int]], near: int | None) -> tuple[int, int] | None:
    if not spans:
        return None
    if near is None:
        return spans[0]
    return min(spans, key=lambda s: (abs(s[0] - near), s[0]))


def locate(haystack: str, needle: str, near: int | None = None) -> tuple[int, int] | None:
    """Span of `needle` in `haystack`, or None.

    With several matches, the one starting closest to `near`, or the first.
    """
    return _nearest(_matches(haystack, needle), near)


def _locate_quote(transcript: str, quote: str, near: int | None) -> tuple[int, int] | None:
    found = locate(transcript, quote, near)
    if found or not _ELLIPSIS_RE.search(quote):
        return found

    pieces = [p for p in _ELLIPSIS_RE.split(quote) if p.strip()]
    if len(pieces) < 2:
        return locate(transcript, pieces[0], near) if pieces else None

    first = locate(transcript, pieces[0], near)
    if first is None:
        return None
    last = _nearest(
        [s for s in _matches(transcript, pieces[-1]) if s[0] >= first[1]], first[1]
    )
    if last is None or last[0] - first[1] > _MAX_ELIDED_GAP:
        return first
    return first[0], last[1]


def _clean_answer(answer: str) -> str:
    return answer.strip().rstrip(_TRAILING_PUNCT).strip()


def find_anchor(
    transcript: str, answer: str, quote: str, near: int | None = None
) -> Anchor | None:
    """Where to underline a card in `transcript`, or None if it is not there.

    In order of preference:

    1. The answer, inside the card's source quote. Looking inside the quote first
       is what makes short answers safe: "3" or "DNA" appears all over a page,
       but only one of those is the one this card is about.
    2. The source quote itself, when the answer is not printed word for word.
    3. The answer anywhere on the page, but only when it appears exactly once
       (or `near` says which one) — the quote could not be found, so nothing else
       vouches for which occurrence is meant.

    `near` is a transcript offset that breaks ties between equal matches; the
    review dialog passes it for a card the user just made from a selection.
    """
    if not transcript.strip():
        return None

    target = _clean_answer(answer)

    span = _locate_quote(transcript, quote, near) if quote.strip() else None
    if span is not None:
        inside = _matches(transcript, target, span[0], span[1]) if target else []
        if inside:
            start, end = inside[0]
            return Anchor(start, end, exact=True)
        return Anchor(span[0], span[1], exact=False)

    if not target:
        return None
    anywhere = _matches(transcript, target)
    if len(anywhere) == 1 or (anywhere and near is not None):
        start, end = _nearest(anywhere, near)
        return Anchor(start, end, exact=True)
    return None


# --- rendering -----------------------------------------------------------------

#: Mid-tone colours, legible on both a light and a dark dialog background.
EXACT_COLOR = "#2f7de1"
QUOTE_COLOR = "#c27c0e"

_HREF_PREFIX = "card:"


def card_href(card_id: int) -> str:
    return f"{_HREF_PREFIX}{card_id}"


def card_id_from_href(href: str) -> int | None:
    """The card id a page link points at, or None for any other link."""
    if not href.startswith(_HREF_PREFIX):
        return None
    try:
        return int(href[len(_HREF_PREFIX) :])
    except ValueError:
        return None


def _escape_segment(text: str) -> str:
    return html.escape(text, quote=False).replace("\n", "<br>")


def render_page(
    transcript: str,
    anchors: dict[int, Anchor],
    active: int | None = None,
    *,
    active_bg: str = "#ffe08a",
    active_fg: str = "#000000",
) -> str:
    """The transcript as HTML, with each anchored card underlined as a link.

    `anchors` maps a card id to its place on the page; the link for a card is
    `card_href(id)`. Spans may overlap — two cards often come from one
    sentence — so the text is cut at every span boundary and each piece links to
    the narrowest card covering it, which keeps a short answer clickable inside
    a longer quote. The `active` card is highlighted wherever it reaches.

    Each character of the transcript is one character of the rendered text
    (`<br>` for a newline, and `white-space: pre-wrap` keeps runs of spaces), so
    offsets in the transcript are offsets in the rendered document.
    """
    spans = sorted(
        ((a.start, a.end, cid, a.exact) for cid, a in anchors.items() if a.end > a.start),
        key=lambda s: (s[0], s[1], s[2]),
    )
    cuts = sorted(
        {0, len(transcript)} | {s[0] for s in spans} | {s[1] for s in spans}
    )

    out: list[str] = []
    for a, b in zip(cuts, cuts[1:]):
        if a >= b:
            continue
        text = _escape_segment(transcript[a:b])
        covering = [s for s in spans if s[0] <= a and s[1] >= b]
        if not covering:
            out.append(text)
            continue

        start, end, cid, exact = min(covering, key=lambda s: (s[1] - s[0], s[2]))
        styles = [
            "text-decoration:underline",
            f"color:{EXACT_COLOR if exact else QUOTE_COLOR}",
        ]
        if active is not None and any(s[2] == active for s in covering):
            styles = [
                "text-decoration:underline",
                f"color:{active_fg}",
                f"background-color:{active_bg}",
            ]
        out.append(f'<a href="{card_href(cid)}" style="{";".join(styles)}">{text}</a>')

    return f'<div style="white-space:pre-wrap">{"".join(out)}</div>'
