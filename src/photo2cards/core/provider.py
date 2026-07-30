"""Provider selection.

Everything upstream of this module talks to the `Provider` protocol, so swapping
a user's own key for a server you run later is a config change plus one class —
no changes at the call sites, no changes to the UI.
"""

from __future__ import annotations

from typing import Protocol

from .errors import ConfigError, ProviderError
from .gemini import GeminiProvider
from .models import Card, SourceImage


class Provider(Protocol):
    def generate_cards(self, image: SourceImage, deck_hint: str = "") -> list[Card]: ...


class ProxyProvider:
    """Calls a backend you operate, which holds the real API key.

    Left as a stub deliberately. It exists so the seam is real and tested-against
    rather than hypothetical; fill it in only if you decide to host a service.
    Your endpoint should accept the image, enforce a per-user quota, and return
    the same `{"cards": [...]}` shape the model produces.
    """

    def __init__(self, url: str, token: str) -> None:
        self.url = url
        self.token = token

    def generate_cards(self, image: SourceImage, deck_hint: str = "") -> list[Card]:
        import base64

        from .gemini import _requests, parse_cards

        requests = _requests()
        try:
            resp = requests.post(
                self.url,
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Content-Type": "application/json",
                },
                json={
                    "image_b64": base64.standard_b64encode(image.data).decode("ascii"),
                    "mime_type": image.mime_type,
                    "deck_hint": deck_hint,
                },
                timeout=180,
            )
        except requests.exceptions.RequestException as exc:
            raise ProviderError(f"Could not reach the proxy at {self.url}: {exc}") from exc

        if resp.status_code != 200:
            raise ProviderError(f"Proxy returned HTTP {resp.status_code}: {resp.text[:300]}")
        return parse_cards(resp.text)


def build_provider(config: dict) -> Provider:
    """Construct the provider named by config, with clear errors when unusable."""
    backend = (config.get("backend") or "gemini_direct").strip()

    if backend == "gemini_direct":
        api_key = (config.get("api_key") or "").strip()
        model = (config.get("model") or "").strip()
        if not api_key:
            raise ConfigError(
                "No API key set yet.\n\n"
                "Open Tools → Photo to Flashcards → Settings… to add one. "
                "You can create a free key at https://aistudio.google.com/apikey"
            )
        if not model:
            raise ConfigError(
                "No model selected yet.\n\n"
                "Open Tools → Photo to Flashcards → Settings… and choose one from the list."
            )
        return GeminiProvider(api_key, model)

    if backend == "proxy":
        url = (config.get("proxy_url") or "").strip()
        if not url:
            raise ConfigError("Backend is set to 'proxy' but 'proxy_url' is empty.")
        return ProxyProvider(url, (config.get("proxy_token") or "").strip())

    raise ConfigError(
        f"Unknown backend '{backend}'. Valid values are 'gemini_direct' and 'proxy'."
    )
