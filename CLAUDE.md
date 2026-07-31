# Working on this repo

An Anki add-on (`photo2cards`) that turns a photo of a page into flashcards via
Google's Gemini API. Each user brings their own key; there is no server.

`README.md` explains what it is and why. This file is about how to change it
safely.

## Commands

```sh
python -m pytest                          # must pass; ~0.2s
python -m ruff check src tests build.py   # must pass
python build.py                           # -> dist/photo2cards.ankiaddon
```

CI runs both on every push, on **Python 3.9 and 3.13**.

## The one architectural rule

`src/photo2cards/core/` must never import `anki` or `aqt`. `ui/` is the only
layer allowed to.

This exists because Anki has no test harness: anything reachable without
launching Anki is anything pytest can cover. When adding behaviour, ask which
side of the line it belongs on. Comparison rules, HTML composition, parsing and
validation go in `core/` and get tests. Only Qt wiring and collection access go
in `ui/`.

**If a test in `tests/` ever needs `aqt`, logic has leaked into the wrong layer.**
`tests/conftest.py` puts `src/photo2cards` (not `src/`) on the path for exactly
this reason, so tests import `core.gemini`, never `photo2cards.core.gemini` —
importing the package would execute `__init__.py`, which builds Anki menus.

## Verifying UI and collection code

`ui/` has no pytest coverage by design, but it is not unverifiable. Anki's own
interpreter can drive the real dialogs headlessly:

```sh
"$LOCALAPPDATA/AnkiProgramFiles/.venv/Scripts/python.exe" probe.py
```

That venv is where `anki` and `PyQt6` actually live — `Programs\Anki` is only a
uv launcher. A probe needs three tricks:

- `import anki.collection` **first**; importing `anki.notes` directly hits a
  circular import.
- Insert a synthetic `ModuleType("photo2cards")` with `__path__` pointing at
  `src/photo2cards` into `sys.modules`, to bypass the menu-building `__init__.py`.
- Stub `aqt`, `aqt.utils`, `aqt.operations`; build `aqt.qt` by re-exporting every
  `Q*` name from real `PyQt6`. `mw` must be a real `QWidget` because dialogs
  parent to it. Set `QT_QPA_PLATFORM=offscreen`.

Then build a real `Collection` in a temp dir and assert against it. This catches
what only fails at runtime — item flags, fonts, `setData(role, None)` resets,
button-group exclusivity — and it is faster and less disruptive than asking a
human to restart Anki and click. Keep probes in a scratch directory; they are
verification, not suite material, and must not land in `tests/`.

Real Anki is still required for anything touching the provider or a real photo.

## Constraints that bite

- **Python 3.9 compatibility.** Anki ships a 3.9 interpreter on some release
  lines. `list[str]` or `X | None` in a signature imports fine on 3.13 and raises
  `TypeError` on 3.9. Every module starts with `from __future__ import
  annotations`; ruff's `FA` rules enforce it and the 3.9 CI leg catches the rest.
- **No compiled dependencies.** One zip must run on every OS Anki supports. This
  is why the Gemini client is raw HTTP over Anki's bundled `requests` rather than
  the official SDK, and why `core/imaging.py` uses Qt instead of Pillow.
- **Never block the main thread.** Anki's UI is single-threaded. Network work goes
  through `QueryOp`, collection writes through `CollectionOp`.
- **Anki must be restarted for code changes**, but config changes are live. That
  asymmetry has already produced one false bug report, where a settings toggle
  applied immediately and made stale code look loaded. Check the process start
  time against the file mtime before debugging.

## Anki API notes

- Duplicate detection is not one search. `dupe:` checksums the HTML-stripped
  first field, so it sees through `<b>` but is **case-sensitive**; a field search
  ignores case but compares markup literally. `ui/ops.py:find_duplicate_note_ids`
  unions both and uses `core.dedupe.normalize` as the arbiter.
- Batch a whole user action into one undo entry with `add_custom_undo_entry` /
  `merge_undo_entries`, so it comes back out with one `Ctrl+Z`.
- Address fields on an existing note through `list(note.keys())`, not the note
  type selected in a dialog — the two can drift apart.

## Secrets

Anki writes `src/photo2cards/meta.json` into the working tree when the add-on is
junction-installed, and **it contains the user's real API key**. It is gitignored
and excluded from `build.py` (CI asserts this), but never echo its contents and
never include that path in output.

Publish under the GitHub handle **38911BytesFree** — never a personal username or
home path. That is the value in `manifest.json`'s `author`.

## Releasing

Tag-triggered. `.github/workflows/release.yml` fires on `v*`, re-runs the full
check suite, verifies the tag matches `manifest.json`'s `human_version`, builds
the zip, asserts it carries no `.pyc`/`__pycache__`/`meta.json`, and publishes.

```sh
# 1. bump human_version in src/photo2cards/manifest.json
# 2. add a CHANGELOG.md entry
# 3. commit, then:
git tag -a v0.3.0 -m "v0.3.0"
git push origin main --follow-tags
```

The version check exists because tagging without bumping the manifest ships an
asset that reports the wrong version inside Anki's add-on list.
