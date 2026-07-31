"""Review-and-edit dialog.

Nothing reaches the collection without passing through here. Model output needs
curation, and silently injecting cards into someone's deck is the kind of thing
that is technically reversible and emotionally not.

Two rules shape the table. A row that cannot become a card cannot be ticked, so
an unfinished edit is visibly excluded rather than quietly dropped at add time.
And a front that already exists — in this batch or in the deck — is surfaced
before the user commits, because a duplicate question is only cheap to fix
before it is in the collection.
"""

from __future__ import annotations

from aqt import mw
from aqt.qt import (
    QAbstractItemView,
    QColor,
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
    QTimer,
    QVBoxLayout,
)
from aqt.utils import askUser, showInfo, showWarning, tooltip

from ..core.dedupe import collapse_identical, group_by_front, normalize
from ..core.models import Card, GenerationResult, SourceImage
from ..core.render import split_back_field
from .dupes import Conflict, resolve_duplicates
from .ops import PendingAdd, PendingUpdate, apply_review_op, find_duplicate_note_ids
from .store import get_config, update_config

COL_CHECK, COL_FRONT, COL_BACK, COL_TAGS = range(4)

#: Row washes. Low alpha, because the add-on does not know whether the user is on
#: a light or dark theme and a solid colour only works on one of them.
_TINT_EXISTING = QColor(230, 145, 30, 60)
_TINT_GROUP = QColor(90, 140, 235, 45)
_GREY = QColor(128, 128, 128)

_TIP_INCOMPLETE = "Needs both a front and a back — this card will not be added."
_TIP_EXISTING = (
    "This deck already has a card with this front. Adding will ask you which "
    "version to keep."
)
_TIP_GROUP = (
    "Another card in this batch has the same front. Normally only one of them "
    "should be added."
)


def _existing_answer_and_quote(note_id: int) -> tuple[str, str]:
    """The answer and citation currently stored on a note."""
    note = mw.col.get_note(note_id)
    fields = list(note.keys())
    if len(fields) < 2:
        return "", ""
    return split_back_field(note[fields[1]])


def _summary(added: int, updated: int, skipped: int) -> str:
    parts = []
    if added:
        parts.append(f"Added {added} card(s)")
    if updated:
        parts.append(f"updated {updated}")
    if skipped:
        parts.append(f"skipped {skipped} already in the deck")
    return ", ".join(parts) + "." if parts else "Nothing to add."


class ReviewDialog(QDialog):
    def __init__(self, results: list[GenerationResult], parent=None) -> None:
        super().__init__(parent or mw)
        self.setWindowTitle("Review generated flashcards")
        self.resize(980, 620)
        self.config = get_config()

        self.results = results

        pairs = [(card, result.source) for result in results for card in result.cards]
        # Overlapping photos of the same page are the normal way to use this, so
        # the same card arriving twice is collapsed before the user ever sees it.
        # What survives is the genuinely ambiguous case — one front, two answers —
        # and those are parked next to each other so the choice is visible.
        pairs = collapse_identical(pairs)
        pairs, self.group_ids = group_by_front(pairs)

        self.cards: list[Card] = [card for card, _ in pairs]
        #: Parallel to self.cards — which image each row came from, so the note
        #: gets the right photo attached.
        self.card_sources: list[SourceImage | None] = [source for _, source in pairs]
        #: The user's last deliberate tick. Blanking a field unticks a row; filling
        #: it back in should restore what they had, not impose a default.
        self._user_checked = [True] * len(self.cards)
        #: Note ids in the target deck that already use this row's front.
        self._existing: list[list[int]] = [[] for _ in self.cards]
        #: Set while we mutate items, so our own writes don't re-enter the handler.
        self._suspend = False

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
        self.table.itemChanged.connect(self._on_item_changed)

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

        # What counts as a duplicate depends on where it is going, so both of
        # these invalidate every row's flag.
        self.deck_combo.currentIndexChanged.connect(self._refresh_existing)
        self.notetype_combo.currentIndexChanged.connect(self._refresh_existing)

        # --- buttons --------------------------------------------------------
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.add_button = buttons.addButton(
            "Add to deck", QDialogButtonBox.ButtonRole.AcceptRole
        )
        self.add_button.clicked.connect(self._add)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._refresh_existing()
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

    # --- row state ------------------------------------------------------ #

    def _lookup_existing(self, row: int) -> list[int]:
        front = self.table.item(row, COL_FRONT).text().strip()
        if not front:
            return []
        return find_duplicate_note_ids(
            front, self.deck_combo.currentData(), self.notetype_combo.currentData()
        )

    def _refresh_existing(self) -> None:
        for row in range(self.table.rowCount()):
            self._existing[row] = self._lookup_existing(row)
            self._refresh_row(row)
        self._update_count()

    def _row_tooltip(self, row: int, valid: bool) -> str:
        if not valid:
            return _TIP_INCOMPLETE
        if self._existing[row]:
            return _TIP_EXISTING
        if self.group_ids[row] >= 0:
            return _TIP_GROUP
        return ""

    def _refresh_row(self, row: int) -> None:
        """Re-derive a row's tick, styling and tooltip from its current contents."""
        front = self.table.item(row, COL_FRONT).text().strip()
        back = self.table.item(row, COL_BACK).text().strip()
        valid = bool(front and back)

        if self._existing[row]:
            tint = _TINT_EXISTING
        elif self.group_ids[row] >= 0:
            tint = _TINT_GROUP
        else:
            tint = None
        tip = self._row_tooltip(row, valid)

        was_suspended = self._suspend
        self._suspend = True
        try:
            check = self.table.item(row, COL_CHECK)
            if valid:
                check.setFlags(
                    Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled
                )
                check.setCheckState(
                    Qt.CheckState.Checked
                    if self._user_checked[row]
                    else Qt.CheckState.Unchecked
                )
            else:
                # Dropping the ItemIsUserCheckable flag is the point: an
                # unfinished row is not something to warn about at add time, it
                # is something that should be impossible to select.
                check.setFlags(Qt.ItemFlag.ItemIsEnabled)
                check.setCheckState(Qt.CheckState.Unchecked)

            for column in (COL_CHECK, COL_FRONT, COL_BACK, COL_TAGS):
                item = self.table.item(row, column)
                font = item.font()
                font.setStrikeOut(not valid)
                item.setFont(font)
                if valid:
                    item.setData(Qt.ItemDataRole.ForegroundRole, None)
                else:
                    item.setForeground(_GREY)
                if tint is None:
                    item.setData(Qt.ItemDataRole.BackgroundRole, None)
                else:
                    item.setBackground(tint)
                item.setToolTip(tip)
        finally:
            self._suspend = was_suspended

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if self._suspend:
            return

        row, column = item.row(), item.column()
        if column == COL_CHECK:
            checked = item.checkState() == Qt.CheckState.Checked
            self._user_checked[row] = checked
            if checked and self._ticked_peers(row):
                # Deferred: opening a modal from inside itemChanged re-enters the
                # table's own handling of the click that got us here.
                QTimer.singleShot(0, lambda r=row: self._confirm_shared_front(r))
        elif column in (COL_FRONT, COL_BACK):
            if column == COL_FRONT:
                self._existing[row] = self._lookup_existing(row)
            self._refresh_row(row)

        self._update_count()

    def _ticked_peers(self, row: int) -> list[int]:
        """Other ticked rows that share this row's front."""
        group_id = self.group_ids[row]
        if group_id < 0:
            return []
        return [
            other
            for other in range(self.table.rowCount())
            if other != row
            and self.group_ids[other] == group_id
            and self.table.item(other, COL_CHECK).checkState() == Qt.CheckState.Checked
        ]

    def _confirm_shared_front(self, row: int) -> None:
        peers = self._ticked_peers(row)
        if not peers:
            return

        front = self.table.item(row, COL_FRONT).text().strip()
        if askUser(
            f"{len(peers) + 1} selected cards ask the same question:\n\n{front}\n\n"
            "Adding them all leaves duplicate questions in the deck. Add them anyway?",
            parent=self,
            title="Same question, more than once",
            defaultno=True,
        ):
            return

        self._suspend = True
        try:
            self.table.item(row, COL_CHECK).setCheckState(Qt.CheckState.Unchecked)
        finally:
            self._suspend = False
        self._user_checked[row] = False
        self._update_count()

    def _set_all(self, state: Qt.CheckState) -> None:
        checked = state == Qt.CheckState.Checked
        self._suspend = True
        try:
            for row in range(self.table.rowCount()):
                self._user_checked[row] = checked
                item = self.table.item(row, COL_CHECK)
                # Incomplete rows stay out of it; they are not selectable.
                if item.flags() & Qt.ItemFlag.ItemIsUserCheckable:
                    item.setCheckState(state)
        finally:
            self._suspend = False
        self._update_count()

    def _checked_rows(self) -> list[int]:
        return [
            row
            for row in range(self.table.rowCount())
            if self.table.item(row, COL_CHECK).checkState() == Qt.CheckState.Checked
        ]

    def _update_count(self) -> None:
        rows = self._checked_rows()
        text = f"{len(rows)} of {len(self.cards)} will be added"
        existing = sum(1 for row in rows if self._existing[row])
        if existing:
            text += f" · {existing} already in this deck"
        self.count_label.setText(text)
        self.add_button.setEnabled(bool(rows))

    # --- adding --------------------------------------------------------- #

    def _collect(self, rows: list[int]) -> list[tuple[Card, SourceImage | None]]:
        """Read edited values back out of the table."""
        return [
            (
                Card(
                    front=self.table.item(row, COL_FRONT).text().strip(),
                    back=self.table.item(row, COL_BACK).text().strip(),
                    tags=[
                        t for t in self.table.item(row, COL_TAGS).text().split() if t
                    ],
                    # Not editable in the table — it is verbatim from the page, so
                    # it comes from the original card rather than a cell.
                    source_quote=self.cards[row].source_quote,
                ),
                self.card_sources[row],
            )
            for row in rows
        ]

    def _partition(
        self,
        collected: list[tuple[Card, SourceImage | None]],
        deck_id: int,
        notetype_name: str,
        attach_quote: bool,
    ) -> tuple[list[PendingAdd], list[PendingUpdate], list[Conflict], int]:
        """Sort approved cards into new notes, quote refreshes, conflicts and no-ops.

        A card whose front and answer both match a note already in the deck is
        the same card, however its citation reads — so it is not added again, and
        the only thing worth writing is a fresher source line. A front that
        matches with a different answer is a real decision and goes to the user.
        """
        adds: list[PendingAdd] = []
        updates: list[PendingUpdate] = []
        conflicts: list[Conflict] = []
        unchanged = 0

        for card, source in collected:
            note_ids = find_duplicate_note_ids(card.front, deck_id, notetype_name)
            if not note_ids:
                adds.append(PendingAdd(card=card, source=source))
                continue

            wanted = normalize(card.back)
            twin: tuple[int, str] | None = None
            for note_id in note_ids:
                answer, quote = _existing_answer_and_quote(note_id)
                if normalize(answer) == wanted:
                    twin = (note_id, quote)
                    break

            if twin is None:
                conflicts.append(
                    Conflict(note_id=note_ids[0], card=card, source=source)
                )
                continue

            note_id, quote = twin
            new_quote = card.source_quote if attach_quote else ""
            if new_quote.strip() and normalize(new_quote) != normalize(quote):
                updates.append(
                    PendingUpdate(note_id=note_id, card=card, source=source)
                )
            else:
                unchanged += 1

        return adds, updates, conflicts, unchanged

    def _add(self) -> None:
        collected = self._collect(self._checked_rows())
        if not collected:
            return

        deck_id = self.deck_combo.currentData()
        notetype = self.notetype_combo.currentData()
        extra_tags = list(self.config.get("extra_tags") or [])
        attach_image = bool(self.config.get("attach_source_image", False))
        attach_quote = bool(self.config.get("attach_source_quote", True))

        update_config(default_deck_id=deck_id, default_notetype=notetype)

        adds, updates, conflicts, skipped = self._partition(
            collected, deck_id, notetype, attach_quote
        )

        if conflicts:
            resolved = resolve_duplicates(
                conflicts,
                deck_name=self.deck_combo.currentText(),
                attach_quote=attach_quote,
                parent=self,
            )
            if resolved is None:
                return  # cancelled: nothing written, the review stays open
            updates.extend(resolved)
            skipped += len(conflicts) - len(resolved)

        if not adds and not updates:
            tooltip(_summary(0, 0, skipped), parent=mw)
            self.accept()
            return

        added_count, updated_count = len(adds), len(updates)

        def on_success(_changes) -> None:
            tooltip(_summary(added_count, updated_count, skipped), parent=mw)
            self.accept()

        apply_review_op(
            adds=adds,
            updates=updates,
            deck_id=deck_id,
            notetype_name=notetype,
            extra_tags=extra_tags,
            on_success=on_success,
            parent=self,
            attach_quote=attach_quote,
            attach_image=attach_image,
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
