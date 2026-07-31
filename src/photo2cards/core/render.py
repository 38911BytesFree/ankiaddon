"""Composition of the note's back field.

Lives in core/ rather than ui/ so it can be tested without launching Anki — the
field HTML is the part most likely to break silently, since a bad tag renders as
mangled text on every card rather than raising anything.
"""

from __future__ import annotations

import html
import re

#: Inline styles, because the add-on writes into whatever note type the user
#: picked and cannot rely on that type's stylesheet. `rgba` grey and `opacity`
#: read acceptably on both light and dark card themes without knowing which.
_QUOTE_STYLE = (
    "margin-top:0.9em;padding-top:0.6em;"
    "border-top:1px solid rgba(128,128,128,0.35);"
    "font-size:0.85em;opacity:0.75;text-align:left"
)

_IMAGE_WRAPPER_STYLE = "margin-top:0.9em"

#: Anki does not constrain image width by default, so a phone photo renders at
#: full pixel size and dwarfs the answer.
_IMAGE_STYLE = "max-width:100%;height:auto"


def build_back_field(answer: str, quote: str = "", image_filename: str = "") -> str:
    """Assemble the back of a card: answer, then optional citation, then image.

    `quote` is verbatim text from the page, so it is escaped — a source
    containing `<`, `>` or `&` (common in maths and chemistry) would otherwise be
    swallowed by Anki's HTML rendering or corrupt the rest of the field.
    """
    parts = [answer]

    if quote.strip():
        parts.append(
            f'<div style="{_QUOTE_STYLE}">'
            f"&ldquo;{html.escape(quote.strip())}&rdquo;"
            f"</div>"
        )

    if image_filename:
        parts.append(
            f'<div style="{_IMAGE_WRAPPER_STYLE}">'
            f'<img src="{html.escape(image_filename, quote=True)}" style="{_IMAGE_STYLE}">'
            f"</div>"
        )

    return "".join(parts)


#: Anchored at the end, because build_back_field only ever appends these. Keying
#: off the exact style strings above means we strip what this add-on wrote and
#: nothing else.
_IMAGE_RE = re.compile(
    r'<div style="' + re.escape(_IMAGE_WRAPPER_STYLE) + r'">\s*<img\b[^>]*>\s*</div>\s*$',
    re.DOTALL,
)
_QUOTE_RE = re.compile(
    r'<div style="' + re.escape(_QUOTE_STYLE) + r'">(.*?)</div>\s*$',
    re.DOTALL,
)


def split_back_field(back: str) -> tuple[str, str]:
    """Undo `build_back_field`: return `(answer, quote)` from a stored back.

    Comparing two cards means comparing their answers, so the citation and the
    photo have to come back off first — the same card re-shot from a different
    angle carries a different quote and must still count as the same card.

    A back that was hand-edited in Anki's editor will not match these patterns
    and is returned whole, with an empty quote. That is deliberate: an
    unrecognised back is a genuine difference, not a false match, and it is safer
    to send it to the user for a decision than to guess at its structure.
    """
    answer = (back or "").rstrip()
    answer = _IMAGE_RE.sub("", answer).rstrip()

    quote = ""
    match = _QUOTE_RE.search(answer)
    if match:
        # The quote was escaped and wrapped in typographic quotes on the way in.
        quote = html.unescape(match.group(1)).strip().strip("“”").strip()
        answer = answer[: match.start()].rstrip()

    return answer, quote


def answer_of(back: str) -> str:
    """The answer portion of a stored back field, decorations removed."""
    return split_back_field(back)[0]
