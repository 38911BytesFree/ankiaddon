# Photo to Flashcards

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

**From a release:** download `photo2cards.ankiaddon` and open it, or use
Anki → Tools → Add-ons → Install from file.

**From source:**

```sh
python build.py            # -> dist/photo2cards.ankiaddon
```

For live development, symlink the package straight into Anki's add-ons folder so
edits take effect on restart:

```sh
# Windows (run as admin)
mklink /D "%APPDATA%\Anki2\addons21\photo2cards" "C:\Users\jpanv\gitdev\ankiaddon\src\photo2cards"

# macOS / Linux
ln -s "$PWD/src/photo2cards" ~/.local/share/Anki2/addons21/photo2cards
```

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

Added cards land as one undoable batch (`Ctrl+Z`).

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
│  ├─ imaging.py       downscale + JPEG encode (Qt)
│  ├─ ratelimit.py     client-side throttle
│  └─ errors.py        typed exceptions the UI branches on
├─ ui/                 Qt + Anki layer
│  ├─ setup.py         settings / first-run dialog
│  ├─ capture.py       file picker, clipboard
│  ├─ review.py        review-and-edit dialog
│  ├─ ops.py           QueryOp / CollectionOp wrappers
│  └─ store.py         config read/write
tests/                 pure-logic tests, no Anki needed
build.py               -> dist/photo2cards.ankiaddon
```

The `core/` vs `ui/` split is the load-bearing decision. Anki has no real test
harness, so anything that can be verified without launching Anki must live where
pytest can reach it. If a test in `tests/` ever needs `aqt`, logic has leaked into
the wrong layer.

## Development

```sh
pip install pytest ruff
python -m pytest
python -m ruff check src tests build.py
```

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
