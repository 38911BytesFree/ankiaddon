"""Resolving a front that is already in the deck with a different back.

Anki's Add dialog flags a duplicate front and stops there, which is the right
call when you are typing a card and can see what you meant. Here the user asked
for these cards and is looking at a dozen of them, so the useful question is not
"is this a duplicate" but "which of these two answers do you want" — and that
cannot be answered without both backs on screen.

Nothing is overwritten by default. Every group starts on "keep what you have".
"""

from __future__ import annotations

from dataclasses import dataclass

from aqt import mw
from aqt.qt import (
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QLabel,
    QRadioButton,
    QScrollArea,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from ..core.models import Card, SourceImage
from ..core.render import build_back_field
from .ops import PendingUpdate


@dataclass
class Conflict:
    """A generated card whose front is in the deck already, under a different back."""

    note_id: int
    card: Card
    source: SourceImage | None


class DuplicateDialog(QDialog):
    def __init__(
        self,
        conflicts: list[Conflict],
        deck_name: str,
        attach_quote: bool,
        parent=None,
    ) -> None:
        super().__init__(parent or mw)
        self.setWindowTitle("Duplicates detected")
        self.resize(840, 660)
        self._attach_quote = attach_quote

        # The choice belongs to the existing note, not to the incoming card: more
        # than one new card can land on the same note, and only one of them can
        # win. Grouping this way makes "at most one replacement" structural
        # rather than something to enforce afterwards.
        by_note: dict[int, list[Conflict]] = {}
        for conflict in conflicts:
            by_note.setdefault(conflict.note_id, []).append(conflict)

        self._choices: list[tuple[list[Conflict], QButtonGroup]] = []

        layout = QVBoxLayout(self)

        intro = QLabel(
            f"{len(by_note)} card(s) in <b>{deck_name}</b> already ask these questions, "
            "with a different answer. Nothing is changed unless you pick a replacement."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        inner = QWidget()
        inner_layout = QVBoxLayout(inner)
        for note_id, group in by_note.items():
            inner_layout.addWidget(self._build_section(note_id, group))
        inner_layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(inner)
        layout.addWidget(scroll, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Apply")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # ------------------------------------------------------------------ #

    def _build_section(self, note_id: int, group: list[Conflict]) -> QFrame:
        frame = QFrame()
        frame.setFrameShape(QFrame.Shape.StyledPanel)
        box = QVBoxLayout(frame)

        note = mw.col.get_note(note_id)
        fields = list(note.keys())
        front = note[fields[0]] if fields else group[0].card.front
        existing_back = note[fields[1]] if len(fields) > 1 else ""

        heading = QLabel(f"<b>{front}</b>")
        heading.setWordWrap(True)
        box.addWidget(heading)

        # "Keep" and the replacements share one exclusive group, so choosing a
        # replacement cannot leave a second one selected.
        options = QButtonGroup(frame)
        keep = QRadioButton("Keep the card already in the deck")
        keep.setChecked(True)
        options.addButton(keep, 0)

        box.addWidget(self._preview(existing_back))
        box.addWidget(keep)

        for index, conflict in enumerate(group, start=1):
            replace = QRadioButton("Replace its back with this:")
            options.addButton(replace, index)
            box.addWidget(replace)
            box.addWidget(
                self._preview(
                    build_back_field(
                        conflict.card.back,
                        quote=conflict.card.source_quote if self._attach_quote else "",
                    )
                )
            )

        self._choices.append((group, options))
        return frame

    def _preview(self, back_html: str) -> QTextBrowser:
        view = QTextBrowser()
        # Lets an <img> in an existing note resolve out of the media folder
        # instead of rendering as a broken-image box. The incoming card's photo
        # is not in the media folder yet, so its preview is text only.
        view.setSearchPaths([mw.col.media.dir()])
        view.setHtml(back_html)
        view.setMaximumHeight(150)
        return view

    def selections(self) -> list[PendingUpdate]:
        """The replacements the user chose. Groups left on "keep" contribute nothing."""
        chosen: list[PendingUpdate] = []
        for group, options in self._choices:
            index = options.checkedId()
            if index > 0:
                conflict = group[index - 1]
                chosen.append(
                    PendingUpdate(
                        note_id=conflict.note_id,
                        card=conflict.card,
                        source=conflict.source,
                    )
                )
        return chosen


def resolve_duplicates(
    conflicts: list[Conflict],
    deck_name: str,
    attach_quote: bool,
    parent=None,
) -> list[PendingUpdate] | None:
    """Ask which version to keep. Returns the accepted updates, or None if cancelled."""
    dialog = DuplicateDialog(conflicts, deck_name, attach_quote, parent)
    if not dialog.exec():
        return None
    return dialog.selections()
