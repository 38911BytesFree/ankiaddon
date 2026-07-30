# Photo to Flashcards — configuration

Most people should use **Tools → Photo to Flashcards → Settings…** instead of editing this
directly. The dialog validates your key and fills in `model` for you.

| Key | Meaning |
|---|---|
| `backend` | `gemini_direct` (your own key, default) or `proxy` (a server you run). |
| `api_key` | Your Google AI Studio key. **Stored in plain text** — see the warning below. |
| `model` | Model id, e.g. `gemini-2.5-flash`. Leave empty and use Settings… to pick from the models your key can actually reach. |
| `proxy_url` | Only used when `backend` is `proxy`. Full URL of your `/generate` endpoint. |
| `proxy_token` | Bearer token sent to your proxy. Not a Google key. |
| `max_image_edge` | Longest edge in pixels before upload. Lower = cheaper and faster, less legible on dense pages. |
| `jpeg_quality` | 1–100. 85 is a good default for text. |
| `requests_per_minute` | Client-side throttle. Keep at or below your tier's limit (free tier is commonly 15). |
| `default_deck_id` | Deck preselected in the review dialog. `0` = whichever deck you used last. |
| `default_notetype` | Note type name. Must have two fields; the first gets the front, the second the back. |
| `attach_source_image` | Store the source photo in your collection's media and link it on each note. |
| `extra_tags` | Tags added to every generated note, on top of the model's own tags. |

## About the API key

Anki has no OS keychain integration, so this key sits in plain text in this add-on's
`meta.json`, inside your Anki data folder. That means:

- Anyone with access to your computer account can read it.
- It is included in a full backup of your Anki folder.
- **Do not** commit `meta.json` to a public repo.

Use a key scoped to a throwaway Google Cloud project, and revoke it in AI Studio if it
ever leaks. Because each user supplies their own key, a leak affects only that user.
