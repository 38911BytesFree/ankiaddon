"""Review-and-edit dialog.

Nothing reaches the collection without passing through here. Model output needs
curation, and silently injecting cards into someone's deck is the kind of thing
that is technically reversible and emotionally not.

Two rules shape the table. A row that cannot become a card cannot be ticked, so
an unfinished edit is visibly excluded rather than quietly dropped at add time.
And a front that already exists — in this batch or in the deck — is surfaced
before the user commits, because a duplicate question is only cheap to fix
before it is in the collection.

Beside the table sits the page itself: each photo's transcription, with every
card that will be added underlined where it came from. Clicking an underline
shows its card, removing one unticks the card, and selecting text makes a new
one. The underlines are a view of the table's tick column, not a second copy of
it, so the two cannot disagree.
"""

from __future__ import annotations

import html
from dataclasses import dataclass, replace

from aqt import mw
from aqt.qt import (
    QAbstractItemView,
    QCheckBox,
    QColor,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    Qt,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QTextCursor,
    QTimer,
    QVBoxLayout,
    QWidget,
)
from aqt.utils import askUser, showInfo, showWarning, tooltip

from ..core.anchors import (
    EXACT_COLOR,
    QUOTE_COLOR,
    Anchor,
    card_id_from_href,
    find_anchor,
    locate,
    render_page,
)
from ..core.dedupe import collapse_identical, group_by_front, identity, normalize
from ..core.errors import Photo2CardsError
from ..core.models import Card, GenerationResult, SourceImage, merge_results
from ..core.render import split_back_field
from .dupes import Conflict, resolve_duplicates
from .newcard import ask_new_card
from .ops import (
    PendingAdd,
    PendingUpdate,
    apply_review_op,
    find_duplicate_note_ids,
    generate_in_background,
    write_card_in_background,
)
from .retry import prompt_retry_failed
from .store import get_config, update_config

COL_CHECK, COL_PHOTO, COL_FRONT, COL_BACK, COL_TAGS = range(5)

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


_NO_TRANSCRIPT = (
    "<p style='color:gray'><i>No transcription for this photo.</i></p>"
    "<p style='color:gray'>Google sometimes withholds the full text of published "
    "pages. The cards from it are still in the table, and the summary is still "
    "added to them.</p>"
)


@dataclass
class _Page:
    """One photo as the review shows it. `summary` holds the user's edits."""

    source: SourceImage
    transcript: str
    summary: str


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
    def __init__(
        self,
        results: list[GenerationResult],
        parent=None,
        *,
        deck_hint: str = "",
    ) -> None:
        super().__init__(parent or mw)
        self.setWindowTitle("Review generated flashcards")
        self.resize(1280, 760)
        self.config = get_config()
        self.deck_hint = deck_hint

        self.results = results

        self.pages: list[_Page] = []
        #: id() of a SourceImage -> index into self.pages.
        self._page_of_source: dict[int, int] = {}
        self._register_pages(results)
        self._current_page = 0
        #: Set while the page view is the one changing the table's current row,
        #: so the page does not scroll away from where the user just clicked.
        self._row_from_page = False
        self._suspend_summary = False

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
        #: User's deliberate photo ticks. Defaults to False (off by default).
        self._user_photo_checked = [
            bool(self.config.get("attach_source_image", False))
            and bool(source and source.data)
            for source in self.card_sources
        ]
        #: Note ids in the target deck that already use this row's front.
        self._existing: list[list[int]] = [[] for _ in self.cards]
        #: Where each row sits on its page's transcript, or None.
        self._anchors: list[Anchor | None] = [None] * len(self.cards)
        #: A transcript offset that picks between equal matches, for cards the
        #: user made from a selection; None for generated cards.
        self._near: list[int | None] = [None] * len(self.cards)
        #: Set while we mutate items, so our own writes don't re-enter the handler.
        self._suspend = False

        layout = QVBoxLayout(self)

        self.failure_banner = QFrame()
        self.failure_banner.setStyleSheet(
            "QFrame { background-color: rgba(230, 145, 30, 30); "
            "border: 1px solid rgba(230, 145, 30, 70); border-radius: 4px; padding: 2px; }"
        )
        banner_layout = QHBoxLayout(self.failure_banner)
        banner_layout.setContentsMargins(8, 4, 8, 4)
        self.failure_label = QLabel()
        self.failure_label.setWordWrap(True)
        self.retry_btn = QPushButton("Select & retry failures…")
        self.retry_btn.clicked.connect(self._on_retry_failures)
        banner_layout.addWidget(self.failure_label, 1)
        banner_layout.addWidget(self.retry_btn)
        layout.addWidget(self.failure_banner)
        self._update_failure_banner()

        splitter = QSplitter(Qt.Orientation.Vertical)
        outer = QSplitter(Qt.Orientation.Horizontal)
        outer.addWidget(self._build_page_panel())

        # --- card table ----------------------------------------------------
        self.table = QTableWidget(len(self.cards), 5)
        self.table.setHorizontalHeaderLabels(["", "Photo", "Front", "Back", "Tags"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(COL_CHECK, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(COL_CHECK, 28)
        header.setSectionResizeMode(COL_PHOTO, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(COL_PHOTO, 50)
        header.setSectionResizeMode(COL_FRONT, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(COL_BACK, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(COL_TAGS, QHeaderView.ResizeMode.ResizeToContents)

        for row, card in enumerate(self.cards):
            self._populate_row(
                row,
                card,
                self.card_sources[row],
                checked=self._user_checked[row],
                photo_checked=self._user_photo_checked[row],
            )

        self.table.currentCellChanged.connect(self._on_row_changed)
        splitter.addWidget(self.table)

        # --- provenance pane ------------------------------------------------
        self.quote_view = QTextBrowser()
        self.quote_view.setPlaceholderText(
            "Select a card to see the text it was generated from."
        )
        splitter.addWidget(self.quote_view)
        splitter.setSizes([460, 180])
        outer.addWidget(splitter)
        outer.setSizes([500, 780])
        layout.addWidget(outer, 1)

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

        self.attach_check = QCheckBox("Attach photos")
        self.attach_check.setToolTip(
            "Attach source photo to all cards. Default is off; you can also "
            "toggle individual cards in the table."
        )
        has_any_source = any(s and s.data for s in self.card_sources)
        self.attach_check.setEnabled(has_any_source)
        self.attach_check.setChecked(
            has_any_source
            and bool(self.config.get("attach_source_image", False))
            and all(self._user_photo_checked)
        )
        self.attach_check.toggled.connect(self._on_attach_all_toggled)
        dest.addWidget(self.attach_check)

        self.summary_check = QCheckBox("Attach page summary")
        self.summary_check.setToolTip(
            "Add each page's summary to the cards from that page, in a collapsible "
            "“Page summary” section on the back."
        )
        has_any_summary = any(p.summary for p in self.pages)
        self.summary_check.setEnabled(has_any_summary)
        self.summary_check.setChecked(
            has_any_summary and bool(self.config.get("attach_page_summary", True))
        )
        dest.addWidget(self.summary_check)

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

        for row in range(len(self.cards)):
            self._compute_anchor(row)
        self._refresh_existing()
        self._show_page(0)
        if self.cards:
            self.table.setCurrentCell(0, COL_FRONT)

    # --- page panel ------------------------------------------------------ #

    def _register_pages(self, results: list[GenerationResult]) -> None:
        for result in results:
            if not result.ok or id(result.source) in self._page_of_source:
                continue
            self._page_of_source[id(result.source)] = len(self.pages)
            self.pages.append(
                _Page(
                    source=result.source,
                    transcript=result.transcript,
                    summary=result.summary,
                )
            )

    def _build_page_panel(self) -> QWidget:
        panel = QWidget()
        box = QVBoxLayout(panel)
        box.setContentsMargins(0, 0, 0, 0)

        top = QHBoxLayout()
        top.addWidget(QLabel("Page:"))
        self.page_combo = QComboBox()
        for page in self.pages:
            self.page_combo.addItem(page.source.display_name)
        self.page_combo.currentIndexChanged.connect(self._show_page)
        top.addWidget(self.page_combo, 1)
        box.addLayout(top)

        box.addWidget(QLabel("Summary — added to every card from this page:"))
        self.summary_edit = QPlainTextEdit()
        self.summary_edit.setPlaceholderText("No summary for this page.")
        self.summary_edit.setMaximumHeight(90)
        self.summary_edit.textChanged.connect(self._on_summary_edited)
        box.addWidget(self.summary_edit)

        self.page_view = QTextBrowser()
        # Links here are cards, not URLs: handle the click, never navigate.
        self.page_view.setOpenLinks(False)
        self.page_view.setOpenExternalLinks(False)
        self.page_view.anchorClicked.connect(self._on_anchor_clicked)
        self.page_view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.page_view.customContextMenuRequested.connect(self._on_page_context_menu)
        self.page_view.selectionChanged.connect(self._sync_page_buttons)
        box.addWidget(self.page_view, 1)

        legend = QLabel(
            f"<span style='color:{EXACT_COLOR};text-decoration:underline'>Underlined"
            "</span> answers are word for word from the page; "
            f"<span style='color:{QUOTE_COLOR};text-decoration:underline'>underlined"
            "</span> passages are where an answer not printed on the page came from. "
            "Click one to see its card, or select text to make a new card."
        )
        legend.setWordWrap(True)
        legend.setStyleSheet("font-size: 11px;")
        box.addWidget(legend)

        actions = QHBoxLayout()
        self.new_card_btn = QPushButton("New card from selection…")
        self.new_card_btn.clicked.connect(self._new_card_from_selection)
        self.remove_card_btn = QPushButton("Remove card")
        self.remove_card_btn.setToolTip(
            "Untick the selected card so it is not added. Tick it in the table to "
            "bring it back."
        )
        self.remove_card_btn.clicked.connect(self._remove_current_card)
        actions.addWidget(self.new_card_btn)
        actions.addWidget(self.remove_card_btn)
        actions.addStretch(1)
        box.addLayout(actions)

        self.page_status = QLabel()
        self.page_status.setWordWrap(True)
        self.page_status.setStyleSheet("color: gray; font-size: 11px;")
        box.addWidget(self.page_status)
        return panel

    def _page_index(self, row: int) -> int | None:
        source = self.card_sources[row]
        return None if source is None else self._page_of_source.get(id(source))

    def _compute_anchor(self, row: int) -> None:
        index = self._page_index(row)
        if index is None:
            self._anchors[row] = None
            return
        back_item = self.table.item(row, COL_BACK)
        back = back_item.text() if back_item else self.cards[row].back
        self._anchors[row] = find_anchor(
            self.pages[index].transcript,
            back,
            self.cards[row].source_quote,
            near=self._near[row],
        )

    def _is_checked(self, row: int) -> bool:
        item = self.table.item(row, COL_CHECK)
        return bool(item) and item.checkState() == Qt.CheckState.Checked

    def _show_page(self, index: int) -> None:
        if not (0 <= index < len(self.pages)):
            self.page_view.setHtml("<p style='color:gray'><i>No pages.</i></p>")
            self._sync_page_buttons()
            return
        self._current_page = index
        if self.page_combo.currentIndex() != index:
            self.page_combo.blockSignals(True)
            self.page_combo.setCurrentIndex(index)
            self.page_combo.blockSignals(False)
        self._suspend_summary = True
        try:
            self.summary_edit.setPlainText(self.pages[index].summary)
        finally:
            self._suspend_summary = False
        self._render_page(keep_scroll=False)

    def _on_summary_edited(self) -> None:
        if self._suspend_summary or not self.pages:
            return
        self.pages[self._current_page].summary = self.summary_edit.toPlainText().strip()

    def _render_page(self, keep_scroll: bool = True) -> None:
        """Redraw the current page from the table's state."""
        if not hasattr(self, "page_view") or not self.pages:
            return
        page = self.pages[self._current_page]
        rows = [
            r
            for r in range(self.table.rowCount())
            if self._page_index(r) == self._current_page
        ]
        ticked = [r for r in rows if self._is_checked(r)]

        if not page.transcript:
            self.page_view.setHtml(_NO_TRANSCRIPT)
            self.page_status.setText(
                f"{len(ticked)} of {len(rows)} card(s) from this page selected."
            )
            self._sync_page_buttons()
            return

        anchors = {r: self._anchors[r] for r in ticked if self._anchors[r] is not None}
        palette = self.page_view.palette()
        markup = render_page(
            page.transcript,
            anchors,
            active=self.table.currentRow(),
            active_bg=palette.highlight().color().name(),
            active_fg=palette.highlightedText().color().name(),
        )
        bar = self.page_view.verticalScrollBar()
        position = bar.value()
        self.page_view.setHtml(markup)
        if keep_scroll:
            bar.setValue(position)

        status = f"{len(anchors)} card(s) underlined on this page."
        missing = len(ticked) - len(anchors)
        if missing:
            status += (
                f" {missing} selected card(s) could not be matched to the text "
                "— they are still in the table and will be added."
            )
        self.page_status.setText(status)
        self._sync_page_buttons()

    def _scroll_page_to(self, row: int) -> None:
        anchor = self._anchors[row]
        if anchor is None:
            return
        # render_page keeps transcript offsets equal to document positions.
        document = self.page_view.document()
        cursor = QTextCursor(document)
        cursor.setPosition(min(anchor.start, max(0, document.characterCount() - 1)))
        self.page_view.setTextCursor(cursor)
        self.page_view.ensureCursorVisible()

    def _sync_page_buttons(self) -> None:
        if not hasattr(self, "new_card_btn"):
            return
        has_text = bool(self.pages) and bool(self.pages[self._current_page].transcript)
        self.new_card_btn.setEnabled(has_text and self.page_view.textCursor().hasSelection())
        row = self.table.currentRow() if hasattr(self, "table") else -1
        self.remove_card_btn.setEnabled(row >= 0 and self._is_checked(row))

    def _on_anchor_clicked(self, url) -> None:
        row = card_id_from_href(url.toString())
        if row is None or not (0 <= row < self.table.rowCount()):
            return
        self._row_from_page = True
        try:
            self.table.setCurrentCell(row, COL_FRONT)
            self.table.scrollToItem(self.table.item(row, COL_FRONT))
        finally:
            self._row_from_page = False
        self._render_page()

    def _on_page_context_menu(self, pos) -> None:
        menu: QMenu = self.page_view.createStandardContextMenu(pos)
        menu.addSeparator()
        new_action = menu.addAction("New card from selection…")
        new_action.setEnabled(self.new_card_btn.isEnabled())
        new_action.triggered.connect(self._new_card_from_selection)

        row = card_id_from_href(self.page_view.anchorAt(pos))
        if row is not None and 0 <= row < self.table.rowCount():
            remove_action = menu.addAction("Remove this card")
            remove_action.triggered.connect(lambda _=False, r=row: self._remove_card(r))
        menu.exec(self.page_view.viewport().mapToGlobal(pos))

    def _remove_current_card(self) -> None:
        row = self.table.currentRow()
        if row >= 0:
            self._remove_card(row)

    def _remove_card(self, row: int) -> None:
        """Untick a card. Its underline goes with it; ticking it brings both back."""
        item = self.table.item(row, COL_CHECK)
        if item is None or not self._is_checked(row):
            return
        item.setCheckState(Qt.CheckState.Unchecked)  # _on_item_changed does the rest
        tooltip("Card removed. Tick it in the table to bring it back.", parent=self)

    def _new_card_from_selection(self) -> None:
        if not self.pages:
            return
        page = self.pages[self._current_page]
        cursor = self.page_view.textCursor()
        if not page.transcript or not cursor.hasSelection():
            return

        # Qt reports line breaks in a selection as paragraph/line separators.
        selected = (
            cursor.selectedText().replace("\u2029", "\n").replace("\u2028", "\n").strip()
        )
        if not selected:
            return
        span = locate(page.transcript, selected, near=cursor.selectionStart())
        near = span[0] if span else cursor.selectionStart()
        passage = page.transcript[span[0] : span[1]] if span else selected

        can_generate = (self.config.get("backend") or "gemini_direct") == "gemini_direct"
        question = ask_new_card(passage, can_generate, parent=self)
        if question is None:
            return

        source = page.source
        if question:
            card = Card(front=question, back=passage, source_quote=passage)
            self._add_user_card(card, source, near)
            return

        hint = self.deck_combo.currentText() or self.deck_hint
        try:
            write_card_in_background(
                page.transcript,
                passage,
                hint,
                lambda card: self._add_user_card(card, source, near),
                parent=self,
            )
        except Photo2CardsError as exc:
            showWarning(str(exc), parent=self, title="Photo to Flashcards")

    def _add_user_card(self, card: Card, source: SourceImage, near: int) -> None:
        key = identity(card)
        for row, existing in enumerate(self.cards):
            if identity(existing) == key:
                self.table.setCurrentCell(row, COL_FRONT)
                tooltip("That card is already in the list.", parent=self)
                return

        row = self._append_row(card, source, near=near)
        self._recompute_groups()
        self._refresh_existing()
        self._sync_attach_check()
        self.table.setCurrentCell(row, COL_FRONT)
        self.table.scrollToItem(self.table.item(row, COL_FRONT))
        tooltip("Card added to the list.", parent=self)

    def _append_row(
        self, card: Card, source: SourceImage | None, *, near: int | None = None
    ) -> int:
        """Add a card as a new, ticked row and return its index."""
        row = self.table.rowCount()
        self.table.insertRow(row)

        self.cards.append(card)
        self.card_sources.append(source)
        self._user_checked.append(True)
        has_source = bool(source and source.data)
        photo_on = has_source and bool(self.config.get("attach_source_image", False))
        self._user_photo_checked.append(photo_on)
        self._existing.append([])
        self.group_ids.append(-1)
        self._anchors.append(None)
        self._near.append(near)

        self._populate_row(row, card, source, checked=True, photo_checked=photo_on)
        self._compute_anchor(row)
        return row

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
        self._show_card_details(row)

        # Follow the card onto its page. A click on the page itself already
        # shows the card where the user is looking, so only a selection made in
        # the table scrolls the page.
        index = self._page_index(row)
        if index is not None and index != self._current_page:
            self._show_page(index)
        else:
            self._render_page()
        if not self._row_from_page and index is not None:
            self._scroll_page_to(row)

    def _show_card_details(self, row: int) -> None:
        card = self.cards[row]
        source = self.card_sources[row]
        origin = source.display_name if source else "unknown source"
        front_item = self.table.item(row, COL_FRONT)
        back_item = self.table.item(row, COL_BACK)
        front = front_item.text() if front_item else card.front
        back = back_item.text() if back_item else card.back
        if card.source_quote:
            quote_body = html.escape(card.source_quote)
        else:
            quote_body = "<i>(no source text recorded)</i>"
        explanation_html = ""
        if card.explanation:
            explanation_html = (
                f"<div style='margin-top:6px;margin-bottom:6px;font-size:12px'>"
                f"<b>Explanation:</b> {html.escape(card.explanation)}"
                f"</div>"
            )
        placement = ""
        if self._page_index(row) is not None and self._anchors[row] is None:
            placement = (
                "<div style='color:#888;font-size:11px;margin-top:4px'>"
                "Not underlined: neither the answer nor its source text could be "
                "found in this page's transcription.</div>"
            )
        self.quote_view.setHtml(
            f"<div style='color:#888;font-size:11px'>From {html.escape(origin)}</div>"
            f"<div style='margin-top:6px'><b>Q:</b> {html.escape(front)}</div>"
            f"<div style='margin-top:2px'><b>A:</b> {html.escape(back)}</div>"
            f"{explanation_html}"
            f"<blockquote>{quote_body}</blockquote>"
            f"{placement}"
        )

    def _populate_row(
        self,
        row: int,
        card: Card,
        source: SourceImage | None,
        *,
        checked: bool = True,
        photo_checked: bool = False,
    ) -> None:
        self._suspend = True
        try:
            check = QTableWidgetItem()
            check.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            check.setCheckState(
                Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
            )
            self.table.setItem(row, COL_CHECK, check)

            photo_check = QTableWidgetItem()
            has_source = bool(source and source.data)
            if has_source:
                photo_check.setFlags(
                    Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled
                )
                photo_check.setCheckState(
                    Qt.CheckState.Checked if photo_checked else Qt.CheckState.Unchecked
                )
                photo_check.setToolTip("Attach the source photo to this card")
            else:
                photo_check.setFlags(Qt.ItemFlag.ItemIsEnabled)
                photo_check.setCheckState(Qt.CheckState.Unchecked)
                photo_check.setToolTip("No source photo available")
            self.table.setItem(row, COL_PHOTO, photo_check)

            self.table.setItem(row, COL_FRONT, QTableWidgetItem(card.front))
            self.table.setItem(row, COL_BACK, QTableWidgetItem(card.back))
            self.table.setItem(row, COL_TAGS, QTableWidgetItem(" ".join(card.tags)))
        finally:
            self._suspend = False

    def _update_failure_banner(self) -> None:
        failures = [r for r in self.results if not r.ok]
        if not failures:
            self.failure_banner.setVisible(False)
            return

        self.failure_banner.setVisible(True)
        self.failure_label.setText(
            f"<b>{len(failures)} of {len(self.results)} image(s) could not be processed.</b>"
        )
        self.failure_label.setToolTip(
            "\n\n".join(f"{r.source.display_name}: {r.error}" for r in failures)
        )
        self.retry_btn.setText(f"Select & retry failures ({len(failures)})…")

    def _on_retry_failures(self) -> None:
        failures = [r for r in self.results if not r.ok]
        to_retry = prompt_retry_failed(failures, parent=self)
        if not to_retry:
            return

        hint = self.deck_combo.currentText() or self.deck_hint

        def on_done(retried_results: list[GenerationResult]) -> None:
            self._handle_retry_results(retried_results)

        generate_in_background(to_retry, hint, on_done, parent=self)

    def _recompute_groups(self) -> None:
        """Recompute group ids based on current front texts in the table."""
        buckets: dict[str, list[int]] = {}
        for row in range(self.table.rowCount()):
            front_item = self.table.item(row, COL_FRONT)
            key = normalize(front_item.text() if front_item else "")
            if key:
                buckets.setdefault(key, []).append(row)

        self.group_ids = [-1] * self.table.rowCount()
        group_counter = 0
        for rows in buckets.values():
            if len(rows) > 1:
                for r in rows:
                    self.group_ids[r] = group_counter
                group_counter += 1

    def _handle_retry_results(self, retried_results: list[GenerationResult]) -> None:
        self.results = merge_results(self.results, retried_results)
        known_pages = len(self.pages)
        self._register_pages(retried_results)
        for page in self.pages[known_pages:]:
            self.page_combo.addItem(page.source.display_name)
        if not self.summary_check.isEnabled() and any(p.summary for p in self.pages):
            self.summary_check.setEnabled(True)
            self.summary_check.setChecked(bool(self.config.get("attach_page_summary", True)))
        if known_pages == 0 and self.pages:
            self._show_page(0)

        new_pairs = [
            (card, r.source)
            for r in retried_results
            if r.ok
            for card in r.cards
        ]
        new_pairs = collapse_identical(new_pairs)

        existing_keys = {identity(c): idx for idx, c in enumerate(self.cards)}
        added_count = 0
        merged_count = 0
        for card, source in new_pairs:
            key = identity(card)
            if key in existing_keys:
                idx = existing_keys[key]
                existing_card = self.cards[idx]
                if (
                    card.source_quote.strip()
                    and card.source_quote != existing_card.source_quote
                ):
                    self.cards[idx] = replace(
                        existing_card, source_quote=card.source_quote
                    )
                    self.card_sources[idx] = source
                    self._near[idx] = None
                    self._compute_anchor(idx)
                merged_tags = list(self.cards[idx].tags) + [
                    t for t in card.tags if t not in self.cards[idx].tags
                ]
                if merged_tags != self.cards[idx].tags:
                    self.cards[idx] = replace(self.cards[idx], tags=merged_tags)
                    tag_item = self.table.item(idx, COL_TAGS)
                    if tag_item:
                        self.table.blockSignals(True)
                        tag_item.setText(" ".join(merged_tags))
                        self.table.blockSignals(False)
                merged_count += 1
                continue

            row = self._append_row(card, source)
            added_count += 1
            existing_keys[key] = row

        if added_count > 0 or merged_count > 0:
            self._recompute_groups()
            self._refresh_existing()
            self._update_count()
            self._sync_attach_check()

            msg_parts = []
            if added_count:
                msg_parts.append(f"Added {added_count} card(s)")
            if merged_count:
                msg_parts.append(f"merged {merged_count} duplicate(s)")
            tooltip(" and ".join(msg_parts) + " from retried images.", parent=self)

        self._update_failure_banner()
        self._render_page()

    # --- row state ------------------------------------------------------ #

    def _lookup_existing(self, row: int) -> list[int]:
        front = self.table.item(row, COL_FRONT).text().strip()
        if not front:
            return []
        return find_duplicate_note_ids(
            front, self.deck_combo.currentData(), self.notetype_combo.currentData()
        )

    def _refresh_existing(self) -> None:
        deck_id = self.deck_combo.currentData()
        notetype_name = self.notetype_combo.currentData()
        cache: dict[str, list[int]] = {}
        for row in range(self.table.rowCount()):
            front = self.table.item(row, COL_FRONT).text().strip()
            if not front:
                self._existing[row] = []
            else:
                if front not in cache:
                    cache[front] = find_duplicate_note_ids(front, deck_id, notetype_name)
                self._existing[row] = cache[front]
            self._refresh_row(row)
        self._update_count()
        self._render_page()

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
            photo_check = self.table.item(row, COL_PHOTO)
            has_source = bool(self.card_sources[row] and self.card_sources[row].data)

            if valid:
                check.setFlags(
                    Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled
                )
                check.setCheckState(
                    Qt.CheckState.Checked
                    if self._user_checked[row]
                    else Qt.CheckState.Unchecked
                )
                if has_source:
                    photo_check.setFlags(
                        Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled
                    )
                    photo_check.setCheckState(
                        Qt.CheckState.Checked
                        if self._user_photo_checked[row]
                        else Qt.CheckState.Unchecked
                    )
            else:
                # Dropping the ItemIsUserCheckable flag is the point: an
                # unfinished row is not something to warn about at add time, it
                # is something that should be impossible to select.
                check.setFlags(Qt.ItemFlag.ItemIsEnabled)
                check.setCheckState(Qt.CheckState.Unchecked)
                if has_source:
                    photo_check.setFlags(Qt.ItemFlag.ItemIsEnabled)
                    photo_check.setCheckState(Qt.CheckState.Unchecked)

            for column in (COL_CHECK, COL_PHOTO, COL_FRONT, COL_BACK, COL_TAGS):
                item = self.table.item(row, column)
                if item is None:
                    continue
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
                if column not in (COL_CHECK, COL_PHOTO):
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
        elif column == COL_PHOTO:
            self._user_photo_checked[row] = (
                item.checkState() == Qt.CheckState.Checked
            )
            self._sync_attach_check()
        elif column in (COL_FRONT, COL_BACK):
            if column == COL_FRONT:
                self._existing[row] = self._lookup_existing(row)
            else:
                # The underline follows the answer, so an edited answer may
                # move it — or move it off the page onto its source quote.
                self._compute_anchor(row)
            self._refresh_row(row)
            if row == self.table.currentRow():
                self._show_card_details(row)

        self._update_count()
        self._render_page()

    def _on_attach_all_toggled(self, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        self._suspend = True
        try:
            for row in range(self.table.rowCount()):
                item = self.table.item(row, COL_PHOTO)
                if item and (item.flags() & Qt.ItemFlag.ItemIsUserCheckable):
                    item.setCheckState(state)
                    self._user_photo_checked[row] = checked
        finally:
            self._suspend = False

    def _sync_attach_check(self) -> None:
        if not hasattr(self, "attach_check"):
            return
        checkable_rows = [
            row
            for row in range(self.table.rowCount())
            if self.table.item(row, COL_PHOTO)
            and (self.table.item(row, COL_PHOTO).flags() & Qt.ItemFlag.ItemIsUserCheckable)
        ]
        if not checkable_rows:
            self.attach_check.setEnabled(False)
            self.attach_check.setChecked(False)
            return

        checked_count = sum(
            1
            for r in checkable_rows
            if self.table.item(r, COL_PHOTO).checkState() == Qt.CheckState.Checked
        )
        self.attach_check.blockSignals(True)
        self.attach_check.setChecked(checked_count == len(checkable_rows))
        self.attach_check.blockSignals(False)

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
        self._render_page()

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
        self._render_page()

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

    def _page_summary(self, row: int) -> str:
        index = self._page_index(row)
        return self.pages[index].summary if index is not None else ""

    def _collect(
        self, rows: list[int], attach_summary: bool = False
    ) -> list[tuple[Card, SourceImage | None]]:
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
                    explanation=self.cards[row].explanation,
                    page_summary=self._page_summary(row) if attach_summary else "",
                ),
                self.card_sources[row]
                if self.table.item(row, COL_PHOTO)
                and self.table.item(row, COL_PHOTO).checkState() == Qt.CheckState.Checked
                else None,
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

        cache: dict[str, list[int]] = {}
        for card, source in collected:
            if card.front not in cache:
                cache[card.front] = find_duplicate_note_ids(card.front, deck_id, notetype_name)
            note_ids = cache[card.front]
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
                for nid in note_ids:
                    conflicts.append(
                        Conflict(note_id=nid, card=card, source=source)
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
        attach_summary = self.summary_check.isEnabled() and self.summary_check.isChecked()
        collected = self._collect(self._checked_rows(), attach_summary)
        if not collected:
            return

        deck_id = self.deck_combo.currentData()
        notetype = self.notetype_combo.currentData()
        extra_tags = list(self.config.get("extra_tags") or [])
        attach_quote = bool(self.config.get("attach_source_quote", True))
        attach_image = any(source is not None for _, source in collected)

        update_config(default_deck_id=deck_id, default_notetype=notetype)
        if self.summary_check.isEnabled():
            update_config(attach_page_summary=self.summary_check.isChecked())

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
            # A card is listed once per note it matches, so count cards, not
            # conflicts: one card kept against three notes is one skip.
            replaced = {id(update.card) for update in resolved}
            skipped += len({id(c.card) for c in conflicts} - replaced)

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
            attach_summary=attach_summary,
        )


def show_review(
    results: list[GenerationResult], parent=None, *, deck_hint: str = ""
) -> None:
    """Open the review dialog, or explain why there's nothing to review."""
    if not results:
        return

    total_cards = sum(len(r.cards) for r in results)
    failures = [r for r in results if not r.ok]

    if total_cards == 0:
        if failures:
            to_retry = prompt_retry_failed(failures, parent=parent or mw)
            if to_retry:

                def on_retry_done(retried_results: list[GenerationResult]) -> None:
                    merged = merge_results(results, retried_results)
                    show_review(merged, parent=parent, deck_hint=deck_hint)

                generate_in_background(
                    to_retry, deck_hint, on_retry_done, parent=parent or mw
                )
            return

        showInfo(
            "No flashcards were generated from these images.\n\n"
            "If the page really does contain study material, try a sharper photo "
            "or a higher 'max image size' in settings.",
            parent=parent or mw,
        )
        return

    ReviewDialog(results, parent, deck_hint=deck_hint).exec()
