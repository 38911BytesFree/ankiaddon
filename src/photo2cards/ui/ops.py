"""Background work and collection writes.

Anki's UI is single-threaded; a blocking HTTPS call on the main thread freezes
the whole app. Everything network-bound goes through QueryOp, and every write to
the collection goes through CollectionOp so it lands in the undo history.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from anki.collection import Collection, OpChanges, SearchNode
from aqt import mw
from aqt.operations import CollectionOp, QueryOp

from ..core.dedupe import normalize
from ..core.errors import Photo2CardsError
from ..core.models import Card, GenerationResult, SourceImage
from ..core.provider import build_provider
from ..core.ratelimit import RateLimiter
from ..core.render import build_back_field
from .store import get_config


@dataclass
class PendingAdd:
    """An approved card that will become a new note."""

    card: Card
    source: SourceImage | None


@dataclass
class PendingUpdate:
    """An approved card that will overwrite the back of a note already in the deck."""

    note_id: int
    card: Card
    source: SourceImage | None


def generate_in_background(
    images: list[SourceImage],
    deck_hint: str,
    on_done,
    parent=None,
) -> None:
    """Run generation off the main thread, then hand results to `on_done`.

    Per-image failures are collected into the results rather than raised, so one
    unreadable photo in a batch of ten doesn't discard the other nine. Only
    setup-level failures (bad config, no key) reach the failure handler.
    """
    config = get_config()
    provider = build_provider(config)  # raises ConfigError early, before any work
    limiter = RateLimiter(int(config.get("requests_per_minute", 15)))

    def work(_col) -> list[GenerationResult]:
        results: list[GenerationResult] = []
        total = len(images)

        for index, image in enumerate(images, start=1):
            mw.taskman.run_on_main(
                lambda i=index, t=total: mw.progress.update(
                    label=f"Reading image {i} of {t}…", value=i - 1, max=t
                )
            )
            limiter.acquire(should_cancel=lambda: mw.progress.want_cancel())

            if mw.progress.want_cancel():
                break

            try:
                cards = provider.generate_cards(image, deck_hint)
                results.append(GenerationResult(source=image, cards=cards))
            except Photo2CardsError as exc:
                results.append(GenerationResult(source=image, error=str(exc)))
            except Exception as exc:  # noqa: BLE001 — one bad image must not kill the batch
                results.append(
                    GenerationResult(source=image, error=f"Unexpected error: {exc}")
                )

        return results

    (
        QueryOp(parent=parent or mw, op=work, success=on_done)
        .with_progress("Generating flashcards…")
        .run_in_background()
    )


def _first_field(note_id: int) -> str:
    note = mw.col.get_note(note_id)
    names = list(note.keys())
    return note[names[0]] if names else ""


def find_duplicate_note_ids(front: str, deck_id: int, notetype_name: str) -> list[int]:
    """Notes already in `deck_id` that ask `front`.

    Scoped to the deck the user is about to add to — Anki's `deck:` search takes
    its subdecks with it — and to the chosen note type, since the same question
    under a different note type is a different card and must not be overwritten.

    Two searches, because neither alone agrees with `normalize` about what counts
    as the same question: `dupe:` checksums the HTML-stripped field but is
    case-sensitive, so it sees through `<b>` and misses a capital letter, while a
    field search ignores case but compares the markup literally. Their union is a
    superset and `normalize` is the arbiter, which also makes over-matching from
    a `*` in the text harmless.
    """
    front = front.strip()
    if not front:
        return []

    notetype = mw.col.models.by_name(notetype_name)
    if notetype is None:
        return []

    field_names = [f["name"] for f in notetype["flds"]]
    if not field_names:
        return []

    scope = (
        SearchNode(deck=mw.col.decks.name(deck_id)),
        SearchNode(note=notetype_name),
    )
    candidates: list[int] = []
    for node in (
        SearchNode(dupe=SearchNode.Dupe(notetype_id=notetype["id"], first_field=front)),
        SearchNode(field=SearchNode.Field(field_name=field_names[0], text=front)),
    ):
        for note_id in mw.col.find_notes(mw.col.build_search_string(*scope, node)):
            if note_id not in candidates:
                candidates.append(note_id)

    wanted = normalize(front)
    return [n for n in candidates if normalize(_first_field(n)) == wanted]


def _undo_label(added: int, updated: int) -> str:
    if added and updated:
        return f"Add {added} and update {updated} generated card(s)"
    if updated:
        return f"Update {updated} generated card(s)"
    return f"Add {added} generated card(s)"


def apply_review_op(
    adds: list[PendingAdd],
    updates: list[PendingUpdate],
    deck_id: int,
    notetype_name: str,
    extra_tags: list[str],
    on_success,
    parent=None,
    attach_quote: bool = True,
    attach_image: bool = False,
) -> None:
    """Write a reviewed batch as one undoable operation.

    Adds and updates share a single undo entry, so however many photos a review
    covered, it comes back out with one Ctrl+Z. `attach_image` and `attach_quote`
    are independent because the two answer different needs — the quote verifies
    the fact, the image shows the whole page.
    """

    def work(col: Collection) -> OpChanges:
        notetype = col.models.by_name(notetype_name)
        if notetype is None:
            raise Photo2CardsError(
                f"Note type '{notetype_name}' does not exist in this collection."
            )

        field_names = [f["name"] for f in notetype["flds"]]
        if len(field_names) < 2:
            raise Photo2CardsError(
                f"Note type '{notetype_name}' has fewer than two fields, so there is "
                "nowhere to put the front and back."
            )
        front_field, back_field = field_names[0], field_names[1]

        # One media write per photo, however many cards came off it.
        written: dict[int, str] = {}

        def media_for(source: SourceImage | None) -> str:
            if not attach_image or source is None or not source.data:
                return ""
            key = id(source)
            if key not in written:
                stem = os.path.splitext(source.display_name)[0] or "photo2cards"
                written[key] = col.media.write_data(f"{stem}.jpg", source.data)
            return written[key]

        def back_for(card: Card, source: SourceImage | None) -> str:
            return build_back_field(
                card.back,
                quote=card.source_quote if attach_quote else "",
                image_filename=media_for(source),
                explanation=card.explanation,
            )

        undo_pos = col.add_custom_undo_entry(_undo_label(len(adds), len(updates)))

        for pending in adds:
            note = col.new_note(notetype)
            note[front_field] = pending.card.front
            note[back_field] = back_for(pending.card, pending.source)
            note.tags = sorted(set(pending.card.tags) | set(extra_tags))
            col.add_note(note, deck_id)

        edited = []
        for pending in updates:
            note = col.get_note(pending.note_id)
            # Address the fields through the note's own note type rather than the
            # one selected in the dialog: writing to a field name it does not
            # have would raise, and the two can drift apart mid-review.
            its_fields = list(note.keys())
            if len(its_fields) < 2:
                continue
            note[its_fields[1]] = back_for(pending.card, pending.source)
            note.tags = sorted(set(note.tags) | set(pending.card.tags) | set(extra_tags))
            edited.append(note)

        if edited:
            col.update_notes(edited)

        return col.merge_undo_entries(undo_pos)

    CollectionOp(parent=parent or mw, op=work).success(on_success).run_in_background()
