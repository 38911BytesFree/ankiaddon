"""Dialog for inspecting and retrying failed images from a batch."""

from __future__ import annotations

import html

from aqt import mw
from aqt.qt import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    Qt,
    QVBoxLayout,
    QWidget,
)

from ..core.models import GenerationResult, SourceImage

_RATE_LIMIT_KEYWORDS = ("rate limit", "429", "quota", "503", "demand", "resource_exhausted")


class RetryFailedDialog(QDialog):
    def __init__(self, failed_results: list[GenerationResult], parent=None) -> None:
        super().__init__(parent or mw)
        self.setWindowTitle("Retry Failed Images")
        self.resize(620, 440)
        self._items: list[tuple[QCheckBox, SourceImage]] = []

        layout = QVBoxLayout(self)

        intro = QLabel(
            f"<b>{len(failed_results)} image(s) could not be processed.</b><br>"
            "Select which images you want to retry:"
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        has_rate_limit = any(
            any(kw in r.error.lower() for kw in _RATE_LIMIT_KEYWORDS)
            for r in failed_results
        )
        if has_rate_limit:
            tip_frame = QFrame()
            tip_frame.setStyleSheet(
                "QFrame { background-color: rgba(230, 145, 30, 25); "
                "border: 1px solid rgba(230, 145, 30, 60); border-radius: 4px; padding: 4px; }"
            )
            tip_layout = QHBoxLayout(tip_frame)
            tip_layout.setContentsMargins(8, 4, 8, 4)
            tip_label = QLabel(
                "💡 <i>Tip: Rate limits and capacity spikes are temporary. "
                "Waiting a few moments before retrying usually succeeds.</i>"
            )
            tip_label.setWordWrap(True)
            tip_layout.addWidget(tip_label)
            layout.addWidget(tip_frame)

        # Scroll area for failed images
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        container = QWidget()
        container_layout = QVBoxLayout(container)
        container_layout.setSpacing(10)

        for result in failed_results:
            row_widget = QWidget()
            row_layout = QVBoxLayout(row_widget)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(2)

            cb = QCheckBox(result.source.display_name)
            cb.setChecked(True)
            cb.toggled.connect(self._update_button_state)
            row_layout.addWidget(cb)

            err_text = result.error.strip() or "Unknown error"
            escaped = html.escape(err_text)
            err_label = QLabel(
                f"<span style='color: #888;'>&nbsp;&nbsp;&nbsp;&nbsp;{escaped}</span>"
            )
            err_label.setWordWrap(True)
            row_layout.addWidget(err_label)

            container_layout.addWidget(row_widget)
            self._items.append((cb, result.source))

        container_layout.addStretch(1)
        scroll.setWidget(container)
        layout.addWidget(scroll, 1)

        # Selection helpers
        tools = QHBoxLayout()
        select_all = QPushButton("Select all")
        select_all.clicked.connect(lambda: self._set_all(True))
        select_none = QPushButton("Select none")
        select_none.clicked.connect(lambda: self._set_all(False))
        tools.addWidget(select_all)
        tools.addWidget(select_none)
        tools.addStretch(1)
        layout.addLayout(tools)

        # Buttons
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.retry_btn = self.buttons.addButton(
            "Retry selected", QDialogButtonBox.ButtonRole.AcceptRole
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        self._update_button_state()

    def _set_all(self, state: bool) -> None:
        for cb, _ in self._items:
            cb.setChecked(state)

    def _update_button_state(self) -> None:
        count = len(self.selected_sources())
        self.retry_btn.setText(f"Retry selected ({count})")
        self.retry_btn.setEnabled(count > 0)

    def selected_sources(self) -> list[SourceImage]:
        return [source for cb, source in self._items if cb.isChecked()]


def prompt_retry_failed(
    failed_results: list[GenerationResult], parent=None
) -> list[SourceImage] | None:
    """Open the retry dialog.

    Returns the list of chosen SourceImages, or None if the user cancelled.
    """
    if not failed_results:
        return None
    dialog = RetryFailedDialog(failed_results, parent=parent or mw)
    dialog.setWindowModality(Qt.WindowModality.ApplicationModal)
    if dialog.exec() == QDialog.DialogCode.Accepted:
        selected = dialog.selected_sources()
        return selected if selected else None
    return None
