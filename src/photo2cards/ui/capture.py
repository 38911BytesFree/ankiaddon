"""Getting images in: file picker and clipboard."""

from __future__ import annotations

from aqt import mw
from aqt.qt import QFileDialog

from ..core.imaging import (
    SUPPORTED_SUFFIXES,
    load_clipboard_image,
)
from ..core.models import SourceImage
from .store import get_config

_LAST_DIR_KEY = "photo2cards_last_dir"


def pick_image_paths(parent=None) -> list[str]:
    """Open a file chooser and return the file paths the user selected."""
    patterns = " ".join(f"*{s}" for s in SUPPORTED_SUFFIXES)

    start_dir = mw.pm.profile.get(_LAST_DIR_KEY, "")
    paths, _ = QFileDialog.getOpenFileNames(
        parent or mw,
        "Choose photos of your study material",
        start_dir,
        f"Images ({patterns});;All files (*)",
    )
    if not paths:
        return []

    import os

    mw.pm.profile[_LAST_DIR_KEY] = os.path.dirname(paths[0])
    return list(paths)


def grab_clipboard_image() -> SourceImage:
    """Return the clipboard image. Raises ImageError when there isn't one."""
    config = get_config()
    data, mime = load_clipboard_image(
        max_edge=int(config.get("max_image_edge", 1600)),
        quality=int(config.get("jpeg_quality", 85)),
    )
    return SourceImage(data=data, mime_type=mime, original_path="")
