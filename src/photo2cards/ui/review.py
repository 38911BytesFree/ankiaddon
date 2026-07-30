"""Review-and-edit dialog.

Nothing reaches the collection without passing through here. Model output needs
curation, and silently injecting cards into someone's deck is the kind of thing
that is technically reversible and emotionally not.
"""

from __future__ import annotations

from aqt import mw
from aqt.qt import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSplitter,
    Qt,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QVBoxLayout,
)
from aqt.utils import showInfo, showWarning, tooltip

from ..core.models import Card, GenerationResult, SourceImage
from .ops import add_cards_op
from .store import get_config, update_config

COL_CHECK, COL_FRONT, COL_BACK, COL_TAGS = range(4)


class ReviewDialog(QDialog):
    def __init__(self, results: list[GenerationResult], parent=None) -> None:
        super().__init__(parent or mw)
        self.setWindowTitle("Review generated flashcards")
        self.resize(980, 620)
        self.config = get_config()

        self.results = results
        self.cards: list[Card] = []
        #: Parallel to self.cards — which image each row came from, so the note
        #: gets the right photo attached.
        self.card_sources: list[SourceImage | None] = []

        for result in results:
            for card in result.cards:
                self.cards.append(card)
                self.card_sources.append(result.source)

        layout = QVBoxLayout(self)

        failures = [r for r in results if not r.ok]
        if failures:
            summary = QLabel(
                f"<b>{len(failures)} of {len(results)} image(s) failed.</b> "
                "Hover a row below for details."
            )
            summary.setWordWrap(True)
            summary.setToolTip(
                "\n\n".join(f"{r.source.display_name}: {r.error}" for r in failures)
            )
            layout.addWidget(summary)

        splitter = QSplitter(Qt.Orientation.Vertical)

        # --- card table ----------------------------------------------------
        self.table = QTableWidget(len(self.cards), 4)
        self.table.setHorizontalHeaderLabels(["", "Front", "Back", "Tags"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(COL_CHECK, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(COL_CHECK, 28)
        header.setSectionResizeMode(COL_FRONT, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(COL_BACK, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(COL_TAGS, QHeaderView.ResizeMode.ResizeToContents)

        for row, card in enumerate(self.cards):
            check = QTableWidgetItem()
            check.setFlags(
                Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled
            )
            check.setCheckState(Qt.CheckState.Checked)
            self.table.setItem(row, COL_CHECK, check)
            self.table.setItem(row, COL_FRONT, QTableWidgetItem(card.front))
            self.table.setItem(row, COL_BACK, QTableWidgetItem(card.back))
            self.table.setItem(row, COL_TAGS, QTableWidgetItem(" ".join(card.tags)))

        self.table.currentCellChanged.connect(self._on_row_changed)
        splitter.addWidget(self.table)

        # --- provenance pane ------------------------------------------------
        self.quote_view = QTextBrowser()
        self.quote_view.setPlaceholderText(
            "Select a card to see the text it was generated from."
        )
        splitter.addWidget(self.quote_view)
        splitter.setSizes([440, 140])
        layout.addWidget(splitter, 1)

        # --- selection helpers ----------------------------------------------
        tools = QHBoxLayout()
        select_all = QPushButton("Select all")
        select_all.clicked.connect(lambda: self._set_all(Qt.CheckState.Checked))
        select_none = QPushButton("Select none")
        select_none.clicked.connect(lambda: self._set_all(Qt.CheckState.Unchecked))
        tools.addWidget(select_all)
        tools.addWidget(select_none)
        tools.addStretch(1)
        self.count_label = QLabel()
        tools.addWidget(self.count_label)
        layout.addLayout(tools)
        self.table.itemChanged.connect(lambda _: self._update_count())

        # --- destination ----------------------------------------------------
        dest = QHBoxLayout()
        dest.addWidget(QLabel("Deck:"))
        self.deck_combo = QComboBox()
        for deck in sorted(mw.col.decks.all_names_and_ids(), key=lambda d: d.name):
            self.deck_combo.addItem(deck.name, deck.id)
        self._restore_deck()
        dest.addWidget(self.deck_combo, 2)

        dest.addWidget(QLabel("Note type:"))
        self.notetype_combo = QComboBox()
        for nt in sorted(mw.col.models.all_names_and_ids(), key=lambda n: n.name):
            self.notetype_combo.addItem(nt.name, nt.name)
        saved_nt = self.config.get("default_notetype", "Basic")
        idx = self.notetype_combo.findData(saved_nt)
        if idx >= 0:
            self.notetype_combo.setCurrentIndex(idx)
        dest.addWidget(self.notetype_combo, 1)
        layout.addLayout(dest)

        # --- buttons --------------------------------------------------------
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.add_button = buttons.addButton(
            "Add to deck", QDialogButtonBox.ButtonRole.AcceptRole
        )
        self.add_button.clicked.connect(self._add)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._update_count()
        if self.cards:
            self.table.setCurrentCell(0, COL_FRONT)

    # ------------------------------------------------------------------ #

    def _restore_deck(self) -> None:
        saved = int(self.config.get("default_deck_id", 0))
        idx = self.deck_combo.findData(saved) if saved else -1
        if idx < 0:
            idx = self.deck_combo.findData(mw.col.decks.get_current_id())
        self.deck_combo.setCurrentIndex(max(0, idx))

    def _on_row_changed(self, row: int, *_args) -> None:
        if not (0 <= row < len(self.cards)):
            return
        card = self.cards[row]
        source = self.card_sources[row]
        origin = source.display_name if source else "unknown source"
        quote = card.source_quote or "<i>(no source text recorded)</i>"
        self.quote_view.setHtml(
            f"<div style='color:#888;font-size:11px'>From {origin}</div>"
            f"<blockquote>{quote}</blockquote>"
        )

    def _set_all(self, state: Qt.CheckState) -> None:
        self.table.blockSignals(True)
        for row in range(self.table.rowCount()):
            self.table.item(row, COL_CHECK).setCheckState(state)
        self.table.blockSignals(False)
        self._update_count()

    def _checked_rows(self) -> list[int]:
        return [
            row
            for row in range(self.table.rowCount())
            if self.table.item(row, COL_CHECK).checkState() == Qt.CheckState.Checked
        ]

    def _update_count(self) -> None:
        n = len(self._checked_rows())
        self.count_label.setText(f"{n} of {len(self.cards)} selected")
        self.add_button.setEnabled(n > 0)

    def _collect(self, rows: list[int]) -> list[tuple[Card, SourceImage | None]]:
        """Read edited values back out of the table."""
        collected = []
        for row in rows:
            front = self.table.item(row, COL_FRONT).text().strip()
            back = self.table.item(row, COL_BACK).text().strip()
            if not front or not back:
                continue
            tags = [t for t in self.table.item(row, COL_TAGS).text().split() if t]
            collected.append(
                (
                    Card(
                        front=front,
                        back=back,
                        tags=tags,
                        # Not editable in the table — it is verbatim from the page,
                        # so it comes from the original card rather than a cell.
                        source_quote=self.cards[row].source_quote,
                    ),
                    self.card_sources[row],
                )
            )
        return collected

    def _add(self) -> None:
        rows = self._checked_rows()
        collected = self._collect(rows)
        if not collected:
            showWarning("Every selected card is missing a front or a back.", parent=self)
            return

        deck_id = self.deck_combo.currentData()
        notetype = self.notetype_combo.currentData()
        extra_tags = list(self.config.get("extra_tags") or [])
        attach_image = bool(self.config.get("attach_source_image", False))
        attach_quote = bool(self.config.get("attach_source_quote", True))

        update_config(default_deck_id=deck_id, default_notetype=notetype)

        # Cards from different photos need different images attached, so group by
        # source and submit one op per group.
        groups: dict[int, list[Card]] = {}
        sources: dict[int, SourceImage | None] = {}
        for card, source in collected:
            key = id(source)
            groups.setdefault(key, []).append(card)
            sources[key] = source

        remaining = len(groups)
        added_total = sum(len(g) for g in groups.values())

        def on_success(_changes) -> None:
            nonlocal remaining
            remaining -= 1
            if remaining == 0:
                tooltip(f"Added {added_total} card(s).", parent=mw)
                self.accept()

        for key, cards in groups.items():
            add_cards_op(
                cards=cards,
                deck_id=deck_id,
                notetype_name=notetype,
                source_image=sources[key] if attach_image else None,
                extra_tags=extra_tags,
                on_success=on_success,
                parent=self,
                attach_quote=attach_quote,
            )


def show_review(results: list[GenerationResult], parent=None) -> None:
    """Open the review dialog, or explain why there's nothing to review."""
    total_cards = sum(len(r.cards) for r in results)
    failures = [r for r in results if not r.ok]

    if total_cards == 0:
        if failures:
            showWarning(
                "No cards could be generated.\n\n"
                + "\n\n".join(f"{r.source.display_name}: {r.error}" for r in failures),
                parent=parent or mw,
            )
        else:
            showInfo(
                "No flashcards were generated from these images.\n\n"
                "If the page really does contain study material, try a sharper photo "
                "or a higher 'max image size' in settings.",
                parent=parent or mw,
            )
        return

    ReviewDialog(results, parent).exec()
