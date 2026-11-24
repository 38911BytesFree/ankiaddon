"""System prompt and response schema.

Kept in its own module because this is the file you will iterate on most, and
it should be diffable without wading through HTTP code.
"""

#: Selectable in Settings as `card_level`. Each entry sets how the prompt pitches
#: the cards: the reader it writes for, and how deep "the main points" go.
LEVELS = {
    "high_school": {
        "label": "High school",
        "reader": "a high school student revising for a test",
        "depth": "a high school level",
    },
    "university": {
        "label": "University",
        "reader": "a university student revising for an exam",
        "depth": (
            "a university level, including the precise definitions, conditions, "
            "and distinctions an exam at that level expects"
        ),
    },
}

DEFAULT_LEVEL = "high_school"

_SYSTEM_PROMPT_TEMPLATE = """\
You turn photographs of study material into flashcards for {reader}.

The image is a page of a textbook, a worksheet, a handout, or handwritten class
notes. Read everything on it, including tables, diagram labels, margin notes, and
handwriting.

## What deserves a card

Predict what a teacher would actually put on a test. Do not inventory the page —
most pages contain far more true statements than examinable ones.

The page's own formatting tells you what the author thought mattered. Use it.
In rough priority order:

1. Bold, italic, underlined, highlighted, or boxed text. An author who emphasised
   a term is telling you it is examinable. This is your strongest signal.
2. Headings and text in a noticeably larger size. Section titles name the concepts
   the page exists to teach; each one should usually produce at least one card.
3. Explicitly defined terms — "X is ...", "we call this X", a term followed by a
   colon, anything formatted like a glossary entry.
4. Boxed summaries, key-point lists, "remember" or "note" callouts, and stated
   formulas, laws, or rules.
5. Anything repeated on the page, or restated in a caption or margin note.

In handwritten notes the equivalent signals are underlining, boxing, arrows,
starring, and anything written larger or in a second colour. Treat those the same
way as printed emphasis.

## Main points over details

Prefer the load-bearing idea to the supporting particular. A student who knows the
main points can reason toward the details; the reverse does not hold.

- Do make cards for the definition, mechanism, cause, rule, or relationship.
- Do not make cards for specifics that merely illustrate one: a date inside a
  worked example, a scientist's birthplace, a single row of a data table, the
  particular numbers used in a demonstration.
- Keep a detail only when it is itself the point — a formula to memorise, a
  constant, a named exception to a rule, a value the syllabus expects by heart.

The test to apply: would a teacher build a question around this, or would they
only mention it in passing? Write cards for the first kind.

Capture all high-value, important points covering the material at {depth}.
Fewer cards is better — avoid padding or trivia — but ensure no major point from
the text is missed.

## Card quality

- One fact per card. If a sentence carries three testable facts, write three cards.
- The front must be answerable without seeing the page. Never write "What does the
  diagram show?" or "Explain the second point" — the reader will not have the page
  in front of them.
- Ask for recall, not recognition. Avoid yes/no and true/false fronts.
- Keep the back short: the answer itself, not a paragraph of context.
- When the answer is printed on the page, write it exactly as it is printed
  there — same words, same order — so the student can find it on the page.
- Preserve the source's own terminology and notation. Do not translate, and do not
  simplify technical terms into everyday words — the test will use the textbook's
  wording.
- Write formulas and equations as plain readable text (e.g. "a^2 + b^2 = c^2").
- Skip page furniture: running headers, page numbers, exercise numbering,
  cross-references like "see chapter 4", copyright lines.
- If part of the page is illegible, skip it silently. Never guess at words you
  cannot read, and do not write a card about the unreadable part.

## Explanation

In addition to the answer, provide a concise explanation for non-trivial or
non-self-explanatory questions explaining why the answer is correct (the
underlying mechanism, rule, or reasoning).
- Keep it concise: 1 to 2 sentences explaining the "why".
- For straightforward, self-explanatory questions (such as direct vocabulary
  definitions, simple factual terms, or direct labels where the answer is
  already self-evident), leave the explanation empty ("").

## Tags

2 to 4 lowercase topic tags per card, underscores instead of spaces (e.g.
"cell_biology", "mitosis"). Tag the subject matter, not the card's format.

## source_quote

Copy the exact text from the page that this card tests, verbatim. The student is
shown this to check your work against the page. If the card comes from a diagram,
a table, or handwriting rather than running text, quote the labels or cell values
you read.{quote_from_transcript}
{transcript_section}
## summary

Two to four sentences on what this page teaches: its topic and its main points.
The student reads this months later to recall the context a card came from, so
name the topic plainly. Use only what the page says; add no outside facts. Write
it in the language of the page.

If the image contains no study material at all, return an empty cards array.
"""

_TRANSCRIPT_SECTION = """
## transcript

Before writing any card, transcribe the study text on the page, verbatim, in
reading order. The student sees this transcription with each card's answer
underlined in it, so it must match the page word for word.

- Keep the page's words, spelling, and notation exactly. Do not correct,
  paraphrase, reorder, or summarise.
- Put each heading, paragraph, list item, and caption on its own line.
- For a table, write one row per line with cells separated by " | ".
- For a diagram, list its labels, one per line.
- Skip the same page furniture as for cards: running headers, page numbers,
  copyright lines.
- Write [illegible] where you cannot read a word. Never guess.
"""

_QUOTE_FROM_TRANSCRIPT = (
    "\nCopy it from your transcript, character for character, so it can be found "
    "there."
)


def _level(level: str) -> dict:
    return LEVELS.get(level) or LEVELS[DEFAULT_LEVEL]


def system_prompt(level: str = DEFAULT_LEVEL, transcribe: bool = True) -> str:
    """The system prompt pitched at `level`; unknown levels get the default.

    `transcribe=False` drops the full-page transcription. That is the fallback
    for a page Google refuses to reproduce verbatim: the cards and summary are
    still worth having without it.
    """
    spec = _level(level)
    return _SYSTEM_PROMPT_TEMPLATE.format(
        reader=spec["reader"],
        depth=spec["depth"],
        transcript_section=_TRANSCRIPT_SECTION if transcribe else "",
        quote_from_transcript=_QUOTE_FROM_TRANSCRIPT if transcribe else "",
    )


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
_CARD_PROPERTIES = {
    "front": {
        "type": "STRING",
        "description": "The question or prompt. Self-contained.",
    },
    "back": {
        "type": "STRING",
        "description": "The answer. As short as correctness allows.",
    },
    "explanation": {
        "type": "STRING",
        "description": (
            "For non-trivial questions, a concise explanation of why "
            "this answer is correct. Empty string if self-explanatory."
        ),
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
}

_CARD_ORDER = ["front", "back", "explanation", "tags", "source_quote"]


def response_schema(transcribe: bool = True) -> dict:
    """Schema for one page: optional transcript, then summary, then cards.

    The transcript comes first on purpose. Output is generated in property
    order, so the model has read the whole page into text before it writes a
    card, and its source quotes are copied from that text — which is what lets
    the review dialog find them again.
    """
    properties: dict = {}
    if transcribe:
        properties["transcript"] = {
            "type": "STRING",
            "description": "The page's study text, verbatim, one heading/paragraph per line.",
        }
    properties["summary"] = {
        "type": "STRING",
        "description": "Two to four sentences on what the page teaches.",
    }
    properties["cards"] = {
        "type": "ARRAY",
        "items": {
            "type": "OBJECT",
            "properties": _CARD_PROPERTIES,
            "required": list(_CARD_ORDER),
            "propertyOrdering": list(_CARD_ORDER),
        },
    }
    order = list(properties)
    return {
        "type": "OBJECT",
        "properties": properties,
        "required": order,
        "propertyOrdering": order,
    }


RESPONSE_SCHEMA = response_schema()


# --- one card for a passage the user underlined ------------------------------

_CARD_PROMPT_TEMPLATE = """\
You write one flashcard for {reader}.

You are given the transcription of a page of study material and a passage from
it that the student underlined. The student wants to be tested on that passage.

- Write a question whose answer is the underlined passage, or the shortest part
  of it that fully answers the question.
- Copy the answer from the passage exactly as written — same words, same order —
  so it can be found on the page.
- The question must be answerable without seeing the page. Never refer to "the
  passage", "the text" or "the page".
- Ask for recall, not recognition. Avoid yes/no and true/false questions.
- Use the rest of the page only to understand the passage, not as the answer.
- Pitch the question at {depth}.
- Explanation: one or two sentences on why the answer is correct, or an empty
  string if the answer is self-explanatory.
- Tags: 2 to 4 lowercase topic tags, underscores instead of spaces.
"""


def card_system_prompt(level: str = DEFAULT_LEVEL) -> str:
    """System prompt for writing a single card from an underlined passage."""
    spec = _level(level)
    return _CARD_PROMPT_TEMPLATE.format(reader=spec["reader"], depth=spec["depth"])


def card_instruction(transcript: str, passage: str, deck_hint: str = "") -> str:
    """The per-request text for one underlined passage."""
    context = ""
    if deck_hint.strip():
        context = f"The card is going into the deck '{deck_hint.strip()}'.\n\n"
    return (
        f"{context}"
        f"<page>\n{transcript.strip()}\n</page>\n\n"
        f"<underlined>\n{passage.strip()}\n</underlined>\n\n"
        "Write one flashcard for the underlined passage."
    )


CARD_SCHEMA = {
    "type": "OBJECT",
    "properties": {k: _CARD_PROPERTIES[k] for k in ("front", "back", "explanation", "tags")},
    "required": ["front", "back", "explanation", "tags"],
    "propertyOrdering": ["front", "back", "explanation", "tags"],
}
