"""Photo to Flashcards — entry point.

Keep this file thin: menu wiring and the top-level flow only. Anything with real
logic belongs in core/ (testable) or ui/ (Qt).
"""

from __future__ import annotations

import contextlib

from aqt import mw
from aqt.qt import QAction, QKeySequence
from aqt.utils import showWarning

from .core.errors import ImageError, Photo2CardsError
from .core.models import GenerationResult, SourceImage


def _ensure_configured(parent=None) -> bool:
    """Return True if we have usable settings, prompting for them if not."""
    from .ui.setup import show_settings
    from .ui.store import is_configured

    if is_configured():
        return True
    return show_settings(parent, first_run=True) and is_configured()


def _run(images: list[SourceImage]) -> None:
    from .ui.ops import generate_in_background
    from .ui.review import show_review

    if not images:
        return

    # The current deck name is only a hint to the model; failing to read it must
    # never block generation.
    deck_hint = ""
    with contextlib.suppress(Exception):
        deck_hint = mw.col.decks.current()["name"]

    def on_done(results: list[GenerationResult]) -> None:
        show_review(results)

    try:
        generate_in_background(images, deck_hint, on_done)
    except Photo2CardsError as exc:
        showWarning(str(exc), parent=mw, title="Photo to Flashcards")


def on_from_files() -> None:
    if not _ensure_configured():
        return
    from .ui.capture import pick_image_files

    _run(pick_image_files())


def on_from_clipboard() -> None:
    if not _ensure_configured():
        return
    from .ui.capture import grab_clipboard_image

    try:
        image = grab_clipboard_image()
    except ImageError as exc:
        showWarning(str(exc), parent=mw, title="Photo to Flashcards")
        return
    _run([image])


def on_settings() -> None:
    from .ui.setup import show_settings

    show_settings(mw)


def _build_menu() -> None:
    menu = mw.form.menuTools.addMenu("Photo to Flashcards")

    from_files = QAction("From image file(s)…", mw)
    from_files.triggered.connect(on_from_files)
    menu.addAction(from_files)

    from_clipboard = QAction("From clipboard image", mw)
    from_clipboard.setShortcut(QKeySequence("Ctrl+Shift+V"))
    from_clipboard.triggered.connect(on_from_clipboard)
    menu.addAction(from_clipboard)

    menu.addSeparator()

    settings = QAction("Settings…", mw)
    settings.triggered.connect(on_settings)
    menu.addAction(settings)


_build_menu()
