"""Image loading and downscaling.

Uses Qt rather than Pillow because PyQt6 ships with Anki and Pillow does not.
Qt is a hard dependency of the add-on either way, and `pip install aqt` pulls it
into a test venv, so this module stays unit-testable.
"""

from __future__ import annotations

from typing import NamedTuple

from .errors import ImageError

#: Anything the Qt image plugins read and Google accepts.
SUPPORTED_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff")


class _QtBindings(NamedTuple):
    QBuffer: object
    QByteArray: object
    QIODevice: object
    Qt: object
    QImage: object
    QImageReader: object
    QGuiApplication: object


def _qt() -> _QtBindings:
    try:
        from PyQt6.QtCore import QBuffer, QByteArray, QIODevice, Qt  # noqa: PLC0415
        from PyQt6.QtGui import (  # noqa: PLC0415
            QGuiApplication,
            QImage,
            QImageReader,
        )
    except ImportError as exc:  # pragma: no cover
        raise ImageError("PyQt6 is unavailable — this must run inside Anki.") from exc
    return _QtBindings(
        QBuffer=QBuffer,
        QByteArray=QByteArray,
        QIODevice=QIODevice,
        Qt=Qt,
        QImage=QImage,
        QImageReader=QImageReader,
        QGuiApplication=QGuiApplication,
    )


def encode_qimage(qimage, max_edge: int = 1600, quality: int = 85) -> tuple[bytes, str]:
    """Downscale if needed and encode to JPEG bytes.

    A 12MP phone photo carries no more legible text than a 1600px one once the
    model tiles it, so shrinking before upload is pure savings in both time and
    quota. Returns `(data, mime_type)`.
    """
    qt = _qt()

    if qimage.isNull():
        raise ImageError("The image could not be decoded.")

    if max(qimage.width(), qimage.height()) > max_edge:
        qimage = qimage.scaled(
            max_edge,
            max_edge,
            qt.Qt.AspectRatioMode.KeepAspectRatio,
            qt.Qt.TransformationMode.SmoothTransformation,
        )

    # Drop alpha: JPEG has no alpha channel, and a transparent scan would
    # otherwise encode as black.
    if qimage.hasAlphaChannel():
        from PyQt6.QtGui import QImage as _QI  # noqa: PLC0415
        from PyQt6.QtGui import QPainter  # noqa: PLC0415

        opaque = _QI(qimage.size(), _QI.Format.Format_RGB32)
        opaque.fill(0xFFFFFFFF)

        painter = QPainter(opaque)
        painter.drawImage(0, 0, qimage)
        painter.end()
        qimage = opaque

    buffer_bytes = qt.QByteArray()
    buffer = qt.QBuffer(buffer_bytes)
    buffer.open(qt.QIODevice.OpenModeFlag.WriteOnly)
    if not qimage.save(buffer, "JPEG", max(1, min(100, quality))):
        buffer.close()
        raise ImageError("Failed to encode the image as JPEG.")
    buffer.close()

    return bytes(buffer_bytes), "image/jpeg"


def load_image_file(path: str, max_edge: int = 1600, quality: int = 85) -> tuple[bytes, str]:
    """Read an image from disk, downscaled and JPEG-encoded.

    Applies EXIF orientation (rotation/flip) automatically so phone photos
    taken in portrait mode are not uploaded sideways or inverted.
    """
    qt = _qt()

    reader = qt.QImageReader(path)
    reader.setAutoTransform(True)
    image = reader.read()
    if image.isNull():
        raise ImageError(
            f"Could not read '{path}' as an image. Supported formats: "
            + ", ".join(s.lstrip(".") for s in SUPPORTED_SUFFIXES)
        )
    return encode_qimage(image, max_edge, quality)


def load_clipboard_image(max_edge: int = 1600, quality: int = 85) -> tuple[bytes, str]:
    """Read whatever image is on the clipboard. Raises if there isn't one."""
    qt = _qt()

    clipboard = qt.QGuiApplication.clipboard()
    if clipboard is None:
        raise ImageError("No clipboard is available.")

    image = clipboard.image()
    if image is None or image.isNull():
        raise ImageError(
            "There is no image on the clipboard.\n\n"
            "Copy a screenshot or a photo first, then try again."
        )
    return encode_qimage(image, max_edge, quality)
