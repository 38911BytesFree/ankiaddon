# Changelog

Notable changes per release. Versions match `human_version` in
`src/photo2cards/manifest.json` and the git tag.

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
