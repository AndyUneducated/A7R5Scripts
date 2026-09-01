"""EXIF handling for re-encoded images."""

from __future__ import annotations

import io

from PIL import ExifTags, Image

_ORIENTATION = 0x0112
_IMAGE_WIDTH = 0x0100
_IMAGE_LENGTH = 0x0101
_STRIP_OFFSETS = 0x0111
_STRIP_BYTE_COUNTS = 0x0117
_JPEG_IF_OFFSET = 0x0201
_JPEG_IF_BYTE_COUNT = 0x0202
_MAKER_NOTE = 0x927C
_PIXEL_X_DIMENSION = 0xA002
_PIXEL_Y_DIMENSION = 0xA003

# IFD0 tags that describe the byte layout of the *source* file and become
# meaningless once the image is re-encoded.
_LAYOUT_TAGS = (
    _IMAGE_WIDTH,
    _IMAGE_LENGTH,
    _STRIP_OFFSETS,
    _STRIP_BYTE_COUNTS,
    _JPEG_IF_OFFSET,
    _JPEG_IF_BYTE_COUNT,
)


def normalize_exif(exif_bytes: bytes | None, size: tuple[int, int]) -> bytes | None:
    """
    Adapt source EXIF for a re-encoded, already-upright image of `size`.

    Orientation is dropped because the rotation is baked into the pixels, and
    the pixel dimension tags are rewritten to match the encoded image.
    """
    if not exif_bytes:
        return None

    exif = Image.Exif()
    try:
        exif.load(exif_bytes)
    except Exception:
        return None

    exif.pop(_ORIENTATION, None)
    for tag in _LAYOUT_TAGS:
        exif.pop(tag, None)

    width, height = size
    sub_ifd = exif.get_ifd(ExifTags.IFD.Exif)
    if sub_ifd is not None:
        sub_ifd[_PIXEL_X_DIMENSION] = width
        sub_ifd[_PIXEL_Y_DIMENSION] = height
        # MakerNote is full of absolute file offsets, so it cannot survive
        # being written back at a different position.
        sub_ifd.pop(_MAKER_NOTE, None)

    try:
        return exif.tobytes()
    except Exception:
        return None


def exif_from_jpeg_bytes(data: bytes) -> bytes | None:
    """Read the raw EXIF block out of an in-memory JPEG."""
    try:
        with Image.open(io.BytesIO(data)) as img:
            return img.info.get("exif")
    except Exception:
        return None
