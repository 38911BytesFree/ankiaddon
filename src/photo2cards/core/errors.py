"""Exception types shared by the pure-logic layer.

Every failure the UI needs to explain differently gets its own class, so the UI
can branch on type instead of matching on message text.
"""

# Required for `float | None` in a signature to be legal on Anki's older
# Python 3.9 builds — without it, that annotation raises at import time.
from __future__ import annotations


class Photo2CardsError(Exception):
    """Base class for anything this add-on raises deliberately."""


class ConfigError(Photo2CardsError):
    """Configuration is missing or unusable — e.g. no API key set yet."""


class ImageError(Photo2CardsError):
    """A file could not be read or decoded as an image."""


class AuthError(Photo2CardsError):
    """The key was rejected. Not retryable without user action."""


class RateLimitError(Photo2CardsError):
    """Quota or rate limit hit. Retryable after a wait.

    `retry_after` is seconds, when the server told us; otherwise None.
    """

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class BlockedError(Photo2CardsError):
    """The provider's safety filters declined the image or the response."""


class ProviderError(Photo2CardsError):
    """Any other non-success reply from the provider."""


class ResponseFormatError(Photo2CardsError):
    """The provider replied successfully but not in the shape we asked for."""
