"""Google Generative Language API client.

Raw HTTP against Anki's bundled `requests` rather than the official SDK: an Anki
add-on ships as one zip that must run on every OS and architecture Anki supports,
and the SDK's dependency tree includes compiled wheels. Vendoring those is a
packaging problem we don't need to take on for two endpoints.

Nothing here imports anki or aqt, so it runs under plain pytest.
"""

from __future__ import annotations

import base64
import contextlib
import json

from .errors import (
    AuthError,
    BlockedError,
    ProviderError,
    RateLimitError,
    ResponseFormatError,
)
from .models import Card, SourceImage
from .prompts import RESPONSE_SCHEMA, SYSTEM_PROMPT, user_instruction

API_ROOT = "https://generativelanguage.googleapis.com/v1beta"

#: Generous, because a dense page at high effort is not fast, and a timeout here
#: costs the user a whole request against their quota.
REQUEST_TIMEOUT = 180


def _requests():
    """Import `requests` lazily with an actionable message if it's absent.

    Anki bundles it; a bare pytest venv may not.
    """
    try:
        import requests  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover
        raise ProviderError(
            "The 'requests' library is unavailable. It normally ships with Anki — "
            "if you are running the tests, `pip install requests`."
        ) from exc
    return requests


def _headers(api_key: str) -> dict[str, str]:
    # Key goes in a header, not the query string, so it stays out of proxy logs
    # and crash reports.
    return {"x-goog-api-key": api_key, "Content-Type": "application/json"}


def _raise_for_error_payload(status: int, body: dict) -> None:
    """Translate Google's error envelope into our exception types."""
    err = body.get("error") or {}
    message = err.get("message") or f"HTTP {status}"
    status_text = err.get("status", "")

    if status in (401, 403) or status_text in ("UNAUTHENTICATED", "PERMISSION_DENIED"):
        raise AuthError(
            f"{message}\n\nThe API key was rejected. Check it in Settings, and confirm "
            "the Generative Language API is enabled for its project."
        )
    if status == 429 or status_text == "RESOURCE_EXHAUSTED":
        retry_after = None
        for detail in err.get("details", []):
            if detail.get("@type", "").endswith("RetryInfo"):
                delay = str(detail.get("retryDelay", ""))
                if delay.endswith("s"):
                    with contextlib.suppress(ValueError):
                        retry_after = float(delay[:-1])
        raise RateLimitError(
            f"{message}\n\nYou have hit your quota. Free-tier limits reset each minute; "
            "daily caps reset at midnight Pacific.",
            retry_after=retry_after,
        )
    if status == 400 and "API key not valid" in message:
        raise AuthError(f"{message}\n\nThat key does not look valid.")
    raise ProviderError(f"{message} (HTTP {status})")


def _post(url: str, api_key: str, payload: dict) -> dict:
    requests = _requests()
    try:
        resp = requests.post(
            url, headers=_headers(api_key), json=payload, timeout=REQUEST_TIMEOUT
        )
    except requests.exceptions.Timeout as exc:
        raise ProviderError(
            f"The request timed out after {REQUEST_TIMEOUT}s. The image may be very "
            "large — try lowering 'max_image_edge' in settings."
        ) from exc
    except requests.exceptions.RequestException as exc:
        raise ProviderError(f"Network error contacting Google: {exc}") from exc

    try:
        body = resp.json()
    except ValueError:
        raise ProviderError(
            f"Unreadable reply from Google (HTTP {resp.status_code}): {resp.text[:300]}"
        ) from None

    if resp.status_code != 200:
        _raise_for_error_payload(resp.status_code, body)
    return body


def list_models(api_key: str) -> list[dict]:
    """Return models this key can call `generateContent` on.

    Doubles as key validation during first-run setup: one cheap request tells us
    both that the key works and which model ids actually exist, so we never
    hardcode a model name that may have been renamed or retired.
    """
    requests = _requests()
    try:
        resp = requests.get(
            f"{API_ROOT}/models",
            headers=_headers(api_key),
            params={"pageSize": 200},
            timeout=30,
        )
    except requests.exceptions.RequestException as exc:
        raise ProviderError(f"Network error contacting Google: {exc}") from exc

    try:
        body = resp.json()
    except ValueError:
        raise ProviderError(
            f"Unreadable reply from Google (HTTP {resp.status_code})."
        ) from None

    if resp.status_code != 200:
        _raise_for_error_payload(resp.status_code, body)

    out = []
    for m in body.get("models", []):
        if "generateContent" not in m.get("supportedGenerationMethods", []):
            continue
        # "models/gemini-x" -> "gemini-x"
        model_id = str(m.get("name", "")).split("/", 1)[-1]
        if not model_id:
            continue
        out.append(
            {
                "id": model_id,
                "display_name": m.get("displayName") or model_id,
                "input_token_limit": m.get("inputTokenLimit"),
            }
        )
    out.sort(key=lambda m: m["id"])
    return out


def _build_payload(image: SourceImage, deck_hint: str) -> dict:
    return {
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": [
            {
                "role": "user",
                "parts": [
                    {
                        "inline_data": {
                            "mime_type": image.mime_type,
                            "data": base64.standard_b64encode(image.data).decode("ascii"),
                        }
                    },
                    {"text": user_instruction(deck_hint)},
                ],
            }
        ],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": RESPONSE_SCHEMA,
        },
    }


def _extract_text(body: dict) -> str:
    """Pull the JSON string out of a generateContent reply, or explain why we can't."""
    feedback = body.get("promptFeedback") or {}
    if feedback.get("blockReason"):
        raise BlockedError(
            f"The image was blocked by Google's safety filters "
            f"(reason: {feedback['blockReason']})."
        )

    candidates = body.get("candidates") or []
    if not candidates:
        raise ResponseFormatError("Google returned no candidates for this image.")

    candidate = candidates[0]
    finish = candidate.get("finishReason", "")
    if finish == "SAFETY":
        raise BlockedError("The response was blocked by Google's safety filters.")
    if finish == "RECITATION":
        raise BlockedError(
            "The response was blocked as a suspected verbatim reproduction of "
            "copyrighted material."
        )
    if finish == "MAX_TOKENS":
        raise ResponseFormatError(
            "The reply was cut off before it was complete. This page is unusually "
            "dense — try cropping it into sections."
        )

    parts = (candidate.get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts)
    if not text.strip():
        raise ResponseFormatError("Google returned an empty response.")
    return text


def parse_cards(text: str) -> list[Card]:
    """Turn the model's JSON string into Card objects, dropping unusable rows."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ResponseFormatError(f"Could not parse the reply as JSON: {exc}") from exc

    if not isinstance(data, dict) or not isinstance(data.get("cards"), list):
        raise ResponseFormatError("The reply did not contain a 'cards' list.")

    cards = [Card.from_json(c) for c in data["cards"] if isinstance(c, dict)]
    return [c for c in cards if c.is_usable()]


class GeminiProvider:
    """Talks to Google directly using the user's own key."""

    def __init__(self, api_key: str, model: str) -> None:
        self.api_key = api_key
        self.model = model

    def generate_cards(self, image: SourceImage, deck_hint: str = "") -> list[Card]:
        url = f"{API_ROOT}/models/{self.model}:generateContent"
        body = _post(url, self.api_key, _build_payload(image, deck_hint))
        return parse_cards(_extract_text(body))
