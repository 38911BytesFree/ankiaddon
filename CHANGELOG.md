# Changelog

Notable changes per release. Versions match `human_version` in
`src/photo2cards/manifest.json` and the git tag.

## v0.5.0 — unreleased

Security and stability hardening from an adversarial review, plus a card level
setting.

### Added
- **Card level setting.** Settings → *Card level* chooses *High school* (the
  default, unchanged prompt) or *University*, which asks for the precise
  definitions, conditions and distinctions an exam at that level expects. Stored
  as `card_level`.
- **Release checksum.** Each release carries `photo2cards.ankiaddon.sha256`.
- Privacy and terms notice in Settings and the README.

### Fixed
- **Model output is escaped before it reaches a card.** Fronts and answers were
  written into fields as raw HTML, so text in a photographed page could inject
  markup or script into cards (and `x < 3` rendered wrongly).
- Cancelling a batch keeps the cards from images already processed, and cancel
  now interrupts retry waits. Server-requested retry delays are capped at 60s.
- Photos load in the background instead of freezing Anki, and phone photos are
  rotated according to their EXIF orientation.
- Hand-edited config values are validated and clamped instead of crashing.
- Duplicate detection matches fronts containing `<`, `>`, `&` or quotes, and a
  front matching several notes offers each of them; one new card can replace
  at most one note.
- Retrying an image that failed to load reloads it from disk. Identical cards
  from a retried image merge into the rows already shown.
- The "skipped" summary counts cards, not matching notes.
- `https` is required for the proxy backend; model ids are validated before use.

### Changed
- The release workflow builds with read-only permissions and hash-pinned tools,
  and publishes from a separate job. Actions are pinned by commit SHA.

## v0.4.0 — 2026-09-05

### Added
- **Explanations for non-trivial cards.** The model now generates concise explanations of why the answer is correct for non-trivial questions, rendered directly below the answer. Self-explanatory questions leave it blank. The review dialog surfaces the explanation in the preview pane.
- **Selective photo attachment.** The review table now features a dedicated "Photo" column and an "Attach photos" batch toggle, allowing users to choose exactly which cards get the source photo attached. Off by default.
- **Collapsible photo display on cards.** Attached photos are enclosed in a collapsible `<details><summary>Source photo</summary>...</details>` disclosure so cards do not show the picture by default during review.
- **Backward-compatible duplicate matching.** `split_back_field` strips explanations and both new and legacy image formats, ensuring seamless duplicate detection across collections.

## v0.3.1 — 2026-07-31

- Refined system prompt to focus on high-value high school level points, capturing core concepts while preferring fewer, high-quality cards without arbitrary card count ranges.

## v0.3.0 — 2026-07-31

Duplicate handling, and a review table that cannot produce a card it will not add.

### Added

- **Duplicate detection against the target deck.** Before anything is written, a
  card whose front already exists in the chosen deck (subdecks included) and note
  type is recognised. Identity is front *and* back — the source quote and the
  attached photo are provenance, not content, so the same card re-read off a
  second photo still counts as the same card.
  - Same front and back, fresher quote → the existing note's citation is updated
    in place. No second card.
  - Same front and back, same quote → nothing is written.
  - Same front, different back → a new **Duplicates detected** dialog shows the
    existing back against the new one and asks which to keep. Defaults to keeping
    what is already in the deck; nothing is overwritten without a deliberate
    choice. Where several new cards land on one note, only one can replace it.
- **In-batch duplicate handling.** Overlapping photos of the same page no longer
  produce the same card twice: exact repeats are collapsed before the review
  dialog opens, keeping the later quote and photo and merging tags. Cards that
  share a front but differ in the back are shown adjacent and tinted, and ticking
  more than one asks for confirmation.
- Rows are marked when their front is already in the target deck, refreshed as
  you edit and when the deck or note type changes.

### Changed

- **A row missing a front or a back now unticks itself and cannot be ticked**,
  greyed and struck through, instead of being silently dropped at add time. The
  "every selected card is missing a front or a back" warning is gone — the state
  is visible in the table instead.
- Adds and updates are written by a single `apply_review_op`, so a review of any
  number of photos undoes with one `Ctrl+Z` rather than one per photo.

### Fixed

- The settings dialog showed "Store the source photo with each note" as ticked on
  a fresh profile, while the shipped default is off.

## v0.2.1 — 2026-07-30

- Cards carry the verbatim source quote instead of the whole page image, which is
  usually noise at card size.
- Add-on author is published as the GitHub profile name.

## v0.2.0 — 2026-07-30

- Rewrote the system prompt to target high-school test recall.
- Fixed default model selection steering users onto a dead free tier.

## v0.1.0

- First release: file and clipboard capture, Gemini vision generation, a
  review-and-edit dialog, and bring-your-own-key setup.
