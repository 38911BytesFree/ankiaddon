"""Background work and collection writes.

Anki's UI is single-threaded; a blocking HTTPS call on the main thread freezes
the whole app. Everything network-bound goes through QueryOp, and every write to
the collection goes through CollectionOp so it lands in the undo history.
"""

from __future__ import annotations

import os

from anki.collection import Collection, OpChanges
from aqt import mw
from aqt.operations import CollectionOp, QueryOp

from ..core.errors import Photo2CardsError
from ..core.models import Card, GenerationResult, SourceImage
from ..core.provider import build_provider
from ..core.ratelimit import RateLimiter
from .store import get_config


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


def add_cards_op(
    cards: list[Card],
    deck_id: int,
    notetype_name: str,
    source_image: SourceImage | None,
    extra_tags: list[str],
    on_success,
    parent=None,
) -> None:
    """Add approved cards to the collection as a single undoable operation."""

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

        # Write the photo into the media folder once, not per card.
        media_ref = ""
        if source_image is not None and source_image.data:
            stem = os.path.splitext(source_image.display_name)[0] or "photo2cards"
            filename = col.media.write_data(f"{stem}.jpg", source_image.data)
            media_ref = f'<br><img src="{filename}">'

        # A custom undo entry lets the whole batch collapse into one Ctrl+Z.
        undo_pos = col.add_custom_undo_entry(f"Add {len(cards)} generated card(s)")

        for card in cards:
            note = col.new_note(notetype)
            note[front_field] = card.front
            note[back_field] = card.back + media_ref
            note.tags = sorted(set(card.tags) | set(extra_tags))
            col.add_note(note, deck_id)

        return col.merge_undo_entries(undo_pos)

    CollectionOp(parent=parent or mw, op=work).success(on_success).run_in_background()
