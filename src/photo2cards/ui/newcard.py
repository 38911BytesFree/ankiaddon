"""Asking how to turn an underlined passage into a card.

The passage the user selected becomes the card's answer and source. The only
open question is who writes the question: leave it blank and the provider
writes one, or type it and no request is made at all.
"""

from __future__ import annotations

from aqt import mw
from aqt.qt import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QVBoxLayout,
)


class NewCardDialog(QDialog):
    def __init__(self, passage: str, can_generate: bool, parent=None) -> None:
        super().__init__(parent or mw)
        self.setWindowTitle("New card from underline")
        self.resize(520, 260)
        self._can_generate = can_generate

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Underlined text — this becomes the answer:"))

        shown = QPlainTextEdit(passage)
        shown.setReadOnly(True)
        shown.setMaximumHeight(110)
        layout.addWidget(shown)

        layout.addWidget(QLabel("Question:"))
        self.question_edit = QLineEdit()
        self.question_edit.setPlaceholderText(
            "Optional — leave blank and Gemini writes the question"
            if can_generate
            else "Type the question for this answer"
        )
        layout.addWidget(self.question_edit)

        hint = QLabel(
            "Gemini may shorten the answer to the part of the underlined text that "
            "answers its question. You can edit both in the table afterwards."
            if can_generate
            else "This backend cannot write questions, so type one yourself."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray; font-size: 11px;")
        layout.addWidget(hint)
        layout.addStretch(1)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        self.question_edit.textChanged.connect(self._sync_ok)
        self._sync_ok()

    def _sync_ok(self) -> None:
        typed = bool(self.question().strip())
        ok = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        ok.setText("Add card" if typed else "Write question")
        ok.setEnabled(typed or self._can_generate)

    def question(self) -> str:
        return self.question_edit.text().strip()


def ask_new_card(passage: str, can_generate: bool, parent=None) -> str | None:
    """The question the user typed, `""` to have one written, or None if cancelled."""
    dialog = NewCardDialog(passage, can_generate, parent)
    if not dialog.exec():
        return None
    return dialog.question()
