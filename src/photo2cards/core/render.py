"""Composition of the note's back field.

Lives in core/ rather than ui/ so it can be tested without launching Anki — the
field HTML is the part most likely to break silently, since a bad tag renders as
mangled text on every card rather than raising anything.
"""

from __future__ import annotations

import html

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
