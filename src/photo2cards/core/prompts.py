"""System prompt and response schema.

Kept in its own module because this is the file you will iterate on most, and
it should be diffable without wading through HTTP code.
"""

SYSTEM_PROMPT = """\
You turn photographs of study material into Anki flashcards.

The image is a page of a textbook, a worksheet, a handout, or handwritten class
notes. Read everything on it, including tables, diagram labels, margin notes,
and handwriting. Then write flashcards that would actually help someone learn
this material.

Card quality rules:
- One fact per card. If a sentence contains three testable facts, write three cards.
- The front must be answerable without seeing the page. Never write "What does the
  diagram show?" or "Explain the second point" — the reader will not have the page.
- Prefer asking for recall over recognition. Avoid yes/no fronts.
- Keep the back short: the specific answer, not a paragraph of context.
- Preserve the source's own terminology and notation. Do not translate, and do not
  simplify technical terms into everyday words.
- For formulas and equations, write them in plain readable text (e.g. "a^2 + b^2 = c^2").
- Skip page furniture: headers, page numbers, exercise numbering, "see chapter 4",
  copyright lines.
- If part of the page is illegible, skip it silently. Do not guess at words you
  cannot read, and do not create a card about the illegible part.

Tags: 2-4 lowercase topic tags per card, using underscores instead of spaces
(e.g. "cell_biology", "mitosis"). Tag the subject matter, not the format.

source_quote: copy the exact text from the page that this card tests, verbatim.
This is shown to the user so they can check your work. If the card comes from a
diagram or handwriting rather than printed text, quote the labels you read.

If the image contains no study material at all, return an empty cards array.
"""


def user_instruction(deck_hint: str = "") -> str:
    """Per-request text block that accompanies the image."""
    if deck_hint.strip():
        return (
            f"These cards are going into the deck '{deck_hint.strip()}'. "
            "Use that as context for the subject matter and expected level, but do "
            "not invent content that isn't on the page.\n\n"
            "Generate flashcards from this image."
        )
    return "Generate flashcards from this image."


# Google's structured-output schema is an OpenAPI subset. Uppercase type names
# are accepted across all versions; `additionalProperties` is NOT supported here,
# so unknown keys are tolerated and dropped by Card.from_json instead.
RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "cards": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "front": {
                        "type": "STRING",
                        "description": "The question or prompt. Self-contained.",
                    },
                    "back": {
                        "type": "STRING",
                        "description": "The answer. As short as correctness allows.",
                    },
                    "tags": {
                        "type": "ARRAY",
                        "items": {"type": "STRING"},
                        "description": "2-4 lowercase topic tags, underscores for spaces.",
                    },
                    "source_quote": {
                        "type": "STRING",
                        "description": "Verbatim text from the image this card is based on.",
                    },
                },
                "required": ["front", "back", "tags", "source_quote"],
                "propertyOrdering": ["front", "back", "tags", "source_quote"],
            },
        }
    },
    "required": ["cards"],
}
