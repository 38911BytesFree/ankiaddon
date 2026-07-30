"""Image loading and downscaling.

Uses Qt rather than Pillow because PyQt6 ships with Anki and Pillow does not.
Qt is a hard dependency of the add-on either way, and `pip install aqt` pulls it
into a test venv, so this module stays unit-testable.
"""

from __future__ import annotations

from .errors import ImageError

#: Anything the Qt image plugins read and Google accepts.
SUPPORTED_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff")


def _qt():
    try:
        from PyQt6.QtCore import QBuffer, QByteArray, QIODevice, Qt  # noqa: PLC0415
        from PyQt6.QtGui import QImage  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover
        raise ImageError("PyQt6 is unavailable — this must run inside Anki.") from exc
    return QBuffer, QByteArray, QIODevice, Qt, QImage


def encode_qimage(qimage, max_edge: int = 1600, quality: int = 85) -> tuple[bytes, str]:
    """Downscale if needed and encode to JPEG bytes.

    A 12MP phone photo carries no more legible text than a 1600px one once the
    model tiles it, so shrinking before upload is pure savings in both time and
    quota. Returns `(data, mime_type)`.
    """
    QBuffer, QByteArray, QIODevice, Qt, _QImage = _qt()

    if qimage.isNull():
        raise ImageError("The image could not be decoded.")

    if max(qimage.width(), qimage.height()) > max_edge:
        qimage = qimage.scaled(
            max_edge,
            max_edge,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

    # Drop alpha: JPEG has no alpha channel, and a transparent scan would
    # otherwise encode as black.
    if qimage.hasAlphaChannel():
        from PyQt6.QtGui import QImage as _QI

        opaque = _QI(qimage.size(), _QI.Format.Format_RGB32)
        opaque.fill(0xFFFFFFFF)
        from PyQt6.QtGui import QPainter

        painter = QPainter(opaque)
        painter.drawImage(0, 0, qimage)
        painter.end()
        qimage = opaque

    buffer_bytes = QByteArray()
    buffer = QBuffer(buffer_bytes)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if not qimage.save(buffer, "JPEG", max(1, min(100, quality))):
        buffer.close()
        raise ImageError("Failed to encode the image as JPEG.")
    buffer.close()

    return bytes(buffer_bytes), "image/jpeg"


def load_image_file(path: str, max_edge: int = 1600, quality: int = 85) -> tuple[bytes, str]:
    """Read an image from disk, downscaled and JPEG-encoded."""
    _, _, _, _, QImage = _qt()

    image = QImage(path)
    if image.isNull():
        raise ImageError(
            f"Could not read '{path}' as an image. Supported formats: "
            + ", ".join(s.lstrip(".") for s in SUPPORTED_SUFFIXES)
        )
    return encode_qimage(image, max_edge, quality)


def load_clipboard_image(max_edge: int = 1600, quality: int = 85) -> tuple[bytes, str]:
    """Read whatever image is on the clipboard. Raises if there isn't one."""
    from PyQt6.QtGui import QGuiApplication

    _, _, _, _, QImage = _qt()

    clipboard = QGuiApplication.clipboard()
    if clipboard is None:
        raise ImageError("No clipboard is available.")

    image = clipboard.image()
    if image is None or image.isNull():
        raise ImageError(
            "There is no image on the clipboard.\n\n"
            "Copy a screenshot or a photo first, then try again."
        )
    return encode_qimage(image, max_edge, quality)
