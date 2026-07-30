"""Plain data types passed between the provider layer and the UI."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Card:
    """One proposed flashcard, before the user has approved it."""

    front: str
    back: str
    tags: list[str] = field(default_factory=list)
    source_quote: str = ""
    #: Unchecked rows are skipped when the user confirms. Defaults to on so the
    #: common case is "accept everything".
    selected: bool = True

    @classmethod
    def from_json(cls, raw: dict) -> Card:
        tags = raw.get("tags") or []
        if not isinstance(tags, list):
            tags = []
        return cls(
            front=str(raw.get("front", "")).strip(),
            back=str(raw.get("back", "")).strip(),
            tags=[str(t).strip().replace(" ", "_") for t in tags if str(t).strip()],
            source_quote=str(raw.get("source_quote", "")).strip(),
        )

    def is_usable(self) -> bool:
        return bool(self.front and self.back)


@dataclass
class SourceImage:
    """An image on its way to the provider.

    `data` is already downscaled and encoded; `original_path` is kept only so
    the review dialog can show where a card came from.
    """

    data: bytes
    mime_type: str
    original_path: str = ""

    @property
    def display_name(self) -> str:
        import os

        return os.path.basename(self.original_path) if self.original_path else "pasted image"


@dataclass
class GenerationResult:
    """Outcome of one image. Errors are carried, not raised, so a batch of
    images can partially succeed."""

    source: SourceImage
    cards: list[Card] = field(default_factory=list)
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error
