"""Getting images in: file picker and clipboard."""

from __future__ import annotations

from aqt import mw
from aqt.qt import QFileDialog

from ..core.errors import ImageError
from ..core.imaging import (
    SUPPORTED_SUFFIXES,
    load_clipboard_image,
    load_image_file,
)
from ..core.models import SourceImage
from .store import get_config

_LAST_DIR_KEY = "photo2cards_last_dir"


def pick_image_files(parent=None) -> list[SourceImage]:
    """Open a file chooser and return the images the user selected.

    Unreadable files are reported but don't abort the rest of the selection.
    """
    config = get_config()
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

    images: list[SourceImage] = []
    problems: list[str] = []
    for path in paths:
        try:
            data, mime = load_image_file(
                path,
                max_edge=int(config.get("max_image_edge", 1600)),
                quality=int(config.get("jpeg_quality", 85)),
            )
            images.append(SourceImage(data=data, mime_type=mime, original_path=path))
        except ImageError as exc:
            problems.append(str(exc))

    if problems:
        from aqt.utils import showWarning

        showWarning(
            "Some files could not be read:\n\n" + "\n\n".join(problems), parent=parent or mw
        )

    return images


def grab_clipboard_image() -> SourceImage:
    """Return the clipboard image. Raises ImageError when there isn't one."""
    config = get_config()
    data, mime = load_clipboard_image(
        max_edge=int(config.get("max_image_edge", 1600)),
        quality=int(config.get("jpeg_quality", 85)),
    )
    return SourceImage(data=data, mime_type=mime, original_path="")
