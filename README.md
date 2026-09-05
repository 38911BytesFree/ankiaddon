# Photo to Flashcards

[![CI](https://github.com/38911BytesFree/ankiaddon/actions/workflows/ci.yml/badge.svg)](https://github.com/38911BytesFree/ankiaddon/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

An Anki add-on that turns a photo of a textbook page, worksheet, or handwritten
notes into flashcards, using Google's Gemini vision models.

**Each user supplies their own API key.** No key ships with the add-on, and there
is no server in between.

## Why bring-your-own-key

The obvious design — embed one API key in the add-on — fails twice over:

1. **It can't stay secret.** An add-on is a zip of readable `.py` files. Obfuscation
   doesn't help: the key must exist in memory to be sent, and it goes on the wire
   where any proxy tool reads it.
2. **It wouldn't work anyway.** Free-tier rate limits are scoped to the *key*. One
   embedded key means every user in the world shares ~15 requests/minute — unusable
   past about a dozen active users, and one abusive user kills it for everyone.

Because the free tier is per-user, asking each person for their own key gives every
user their own independent quota. That scales without limit, costs the maintainer
nothing, and removes the secret entirely.

If you later want a hosted option, `core/provider.py` already has the seam — see
[Adding a backend](#adding-a-backend-later).

## Install

**From a release (recommended).** Grab `photo2cards.ankiaddon` from the
[latest release](https://github.com/38911BytesFree/ankiaddon/releases/latest) and
open it, or use **Anki → Tools → Add-ons → Install from file…**. Restart Anki.

**From source.**

```sh
python build.py            # -> dist/photo2cards.ankiaddon
```

A `.ankiaddon` file is a zip with the add-on's files at the *top level* of the
archive — not nested in a folder. `build.py` handles that, and packaging by hand
usually gets it wrong.

**For development**, point Anki's add-ons folder at your working tree instead, so
edits take effect on the next Anki restart with no rebuild. Close Anki first — it
scans the folder at startup.

```cmd
:: Windows. /J makes a junction, which needs no elevated prompt (/D would).
mklink /J "%APPDATA%\Anki2\addons21\photo2cards" "%CD%\src\photo2cards"
```

```sh
# macOS
ln -s "$PWD/src/photo2cards" ~/Library/Application\ Support/Anki2/addons21/photo2cards
# Linux
ln -s "$PWD/src/photo2cards" ~/.local/share/Anki2/addons21/photo2cards
```

Note that Anki will write `meta.json` — containing your API key — into the linked
source folder. It's gitignored and excluded from the build, which is why both of
those exist.

## First run

Tools → **Photo to Flashcards** → Settings…

1. Click **Get a free API key…** — opens Google AI Studio.
2. Paste the key.
3. Click **Verify key & load models** and pick one.

That verify step is a single `ListModels` call. It confirms the key works *and*
discovers which model ids the key can actually reach — so the add-on never
hardcodes a model name that has since been renamed or retired.

## Use

| Action | Where |
|---|---|
| From image files | Tools → Photo to Flashcards → From image file(s)… |
| From clipboard | `Ctrl+Shift+V`, or the menu |

Both open a review dialog. **Nothing is written to your collection until you
approve it there** — edit fronts, backs, and tags inline, untick anything you
don't want, then choose a deck and note type. Selecting a row shows the verbatim
text from the page that the card came from, so you can check the model's work.

Every card is ticked to begin with, so approving the batch is one click. A row
whose front or back you empty unticks itself and greys out, rather than silently
vanishing when you add. Photos are not attached by default; you can toggle photo
attachment for the whole batch or check the "Photo" column for specific cards
(such as diagrams). Attached photos are kept inside a collapsible "Source photo"
disclosure on the card so they never clutter normal review.

### Duplicates

Photographing overlapping pages is normal, and so is running the same page twice,
so the review step checks for cards you already have. A card's identity is its
front and its back — the source quote and photo are provenance, not content, so
the same card re-read off a second photo is still the same card.

| Situation | What happens |
|---|---|
| The same card twice in one batch | Collapsed before you see it, keeping the later quote |
| Same question, different answer, in one batch | Shown side by side and tinted; ticking both asks first |
| Already in the target deck, same answer | Not added again. Its citation is refreshed if yours is newer |
| Already in the target deck, different answer | A **Duplicates detected** dialog shows both backs and asks which to keep |

Nothing already in your collection is overwritten unless you pick the replacement
yourself. The deck check covers the deck you are adding to and its subdecks, for
the note type you selected.

Everything a review writes — new cards and updated ones — lands as one undoable
batch (`Ctrl+Z`).

## Layout

```
src/photo2cards/       the add-on — this is what gets zipped
├─ __init__.py         menu wiring only
├─ manifest.json       package metadata
├─ config.json         default settings
├─ core/               NO anki/aqt imports — testable under plain pytest
│  ├─ gemini.py        HTTP client + error mapping
│  ├─ provider.py      backend selection (direct vs proxy)
│  ├─ prompts.py       system prompt + response schema  ← iterate here
│  ├─ models.py        Card / SourceImage / GenerationResult
│  ├─ dedupe.py        when two cards count as the same card
│  ├─ render.py        composes the note back, and takes it apart again
│  ├─ imaging.py       downscale + JPEG encode (Qt)
│  ├─ ratelimit.py     client-side throttle
│  └─ errors.py        typed exceptions the UI branches on
├─ ui/                 Qt + Anki layer
│  ├─ setup.py         settings / first-run dialog
│  ├─ capture.py       file picker, clipboard
│  ├─ review.py        review-and-edit dialog
│  ├─ dupes.py         "which version do you want" dialog
│  ├─ ops.py           QueryOp / CollectionOp wrappers
│  └─ store.py         config read/write
tests/                 pure-logic tests, no Anki needed
build.py               -> dist/photo2cards.ankiaddon
```

The `core/` vs `ui/` split is the load-bearing decision. Anki has no real test
harness, so anything that can be verified without launching Anki must live where
pytest can reach it. If a test in `tests/` ever needs `aqt`, logic has leaked into
the wrong layer.

`ui/` is still checkable without clicking through Anki: its dialogs can be driven
headlessly against a throwaway collection using Anki's own bundled interpreter.
[CLAUDE.md](CLAUDE.md) has the recipe, along with the constraints and Anki API
quirks worth knowing before changing anything. Release history is in
[CHANGELOG.md](CHANGELOG.md).

## Development

```sh
pip install pytest ruff
python -m pytest
python -m ruff check src tests build.py
```

CI runs both on every push and PR, plus a packaging check. The test matrix
includes **Python 3.9** deliberately: Anki has shipped a 3.9 interpreter across
several release lines, and `float | None` in a function signature imports fine on
3.13 while raising `TypeError` on 3.9 — a bug that only appears once a real user
loads the add-on. Ruff's `FA` rules catch most of that statically; the 3.9 leg
catches the rest.

Two constraints worth remembering when adding code:

- **No compiled dependencies.** The add-on ships as a single zip that must run on
  every OS and architecture Anki supports. That's why this talks to Google over raw
  HTTP with Anki's bundled `requests` instead of using the official SDK — the SDK's
  dependency tree includes platform-specific wheels.
- **Never block the main thread.** Anki's UI is single-threaded; a synchronous HTTP
  call freezes the app. Network work goes through `QueryOp`, collection writes
  through `CollectionOp`.

## Adding a backend later

If you decide to host a service (to remove the key-setup step, or to monetize),
set `backend` to `proxy` in config and fill in `ProxyProvider.generate_cards` in
`core/provider.py`. Your endpoint takes `{image_b64, mime_type, deck_hint}` and
returns the same `{"cards": [...]}` shape. Nothing else changes — not the UI, not
the call sites.

Non-negotiables if you do: per-user auth and quota enforced server-side, input
size/MIME validation, and a hard monthly spend ceiling with alerting. An
unauthenticated proxy is just a slower way to leak your key. You'd also become a
data processor for images of other people's coursework, which wants a privacy
policy first.

## Known limits

- One image per request. Very dense pages can exceed the reply limit — the add-on
  says so and suggests cropping.
- Free-tier daily caps reset at midnight Pacific; the per-minute throttle can't
  help with those.
- The API key is stored unencrypted, because Anki has no keychain integration.
  See `config.md`.

## Licence

MIT — see [LICENSE](LICENSE).
