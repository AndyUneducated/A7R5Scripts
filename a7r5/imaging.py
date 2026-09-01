"""Decoding, resizing and encoding of A7R V images."""

from __future__ import annotations

import io
import pathlib
from dataclasses import dataclass

import rawpy
from PIL import Image, ImageFile, ImageOps

from . import exifmeta

HEIF_ENABLED = False
HEIF_IMPORT_ERROR: str | None = None
try:
    import pillow_heif

    pillow_heif.register_heif_opener()
    HEIF_ENABLED = True
except Exception as exc:  # pragma: no cover - depends on local libheif
    HEIF_IMPORT_ERROR = repr(exc)

RAW_EXTS = {".arw"}
HEIF_EXTS = {".heic", ".heif", ".hif", ".hifc"}
SUPPORTED_IN = {".jpg", ".jpeg", ".png"} | HEIF_EXTS | RAW_EXTS

RAW_WB_MODES = ("camera", "auto", "none")
SUBSAMPLING_MODES = ("auto", "444", "422", "420")

_SUBSAMPLING_CODES = {"444": 0, "422": 1, "420": 2}
_MIN_FIT_EDGE = 320
_MAX_JPEG_BLOCK = 1 << 28

# Pillow's HEIF plugin reads these straight out of Image.info, so they have to
# be cleared before saving for --strip to mean anything.
_INFO_METADATA_KEYS = ("exif", "icc_profile", "xmp", "XML:com.adobe.xmp")


@dataclass(frozen=True)
class Metadata:
    """The metadata blocks carried from the source file to the output."""

    exif: bytes | None = None
    icc: bytes | None = None
    xmp: bytes | None = None


NO_METADATA = Metadata()


@dataclass(frozen=True)
class Decoded:
    """A decoded, upright, RGB image plus its metadata."""

    image: Image.Image
    metadata: Metadata = NO_METADATA


def require_heif(reason: str) -> None:
    """Raise a helpful error when HEIF support is needed but unavailable."""
    if HEIF_ENABLED:
        return
    message = (
        f"{reason}\n"
        "HEIF/HIF support requires pillow-heif (and system libheif).\n"
        "Install:\n"
        "  pip install pillow-heif\n"
        "macOS:\n"
        "  brew install libheif\n"
        "Ubuntu/Debian:\n"
        "  sudo apt-get install -y libheif1 libheif-dev\n"
    )
    if HEIF_IMPORT_ERROR:
        message += f"\nImport error: {HEIF_IMPORT_ERROR}\n"
    raise RuntimeError(message)


def flatten_to_rgb(
    img: Image.Image, bg_rgb: tuple[int, int, int] = (255, 255, 255)
) -> Image.Image:
    """Composite any alpha onto `bg_rgb` and normalize the mode to RGB."""
    if img.mode in ("RGBA", "LA"):
        background = Image.new("RGBA", img.size, bg_rgb + (255,))
        return Image.alpha_composite(background, img.convert("RGBA")).convert("RGB")

    if img.mode == "P":
        if "transparency" in img.info:
            return flatten_to_rgb(img.convert("RGBA"), bg_rgb)
        return img.convert("RGB")

    if img.mode != "RGB":
        return img.convert("RGB")

    return img


def _raw_exif(raw: rawpy.RawPy, path: pathlib.Path) -> bytes | None:
    """
    Recover EXIF for a RAW file.

    rawpy hands back bare pixels, so the metadata is taken from the embedded
    JPEG preview, which carries the camera's full EXIF block.
    """
    try:
        thumb = raw.extract_thumb()
        if thumb.format == rawpy.ThumbFormat.JPEG:
            exif = exifmeta.exif_from_jpeg_bytes(thumb.data)
            if exif:
                return exif
    except Exception:
        pass

    try:
        with Image.open(str(path)) as img:
            return img.info.get("exif")
    except Exception:
        return None


def _decode_raw(
    path: pathlib.Path,
    bg_rgb: tuple[int, int, int],
    raw_wb: str,
    half_size: bool,
) -> Decoded:
    params: dict[str, object] = {
        "no_auto_bright": True,
        "output_bps": 8,
        "gamma": (2.222, 4.5),
        "half_size": half_size,
    }
    if raw_wb == "camera":
        params["use_camera_wb"] = True
    elif raw_wb == "auto":
        params["use_auto_wb"] = True

    with rawpy.imread(str(path)) as raw:
        exif = _raw_exif(raw, path)
        rgb = raw.postprocess(**params)

    # LibRaw already applies the camera's flip, so the array is upright.
    return Decoded(
        flatten_to_rgb(Image.fromarray(rgb), bg_rgb), Metadata(exif=exif)
    )


def decode(
    path: str | pathlib.Path,
    bg_rgb: tuple[int, int, int] = (255, 255, 255),
    raw_wb: str = "camera",
    raw_half_size: bool = False,
) -> Decoded:
    """Decode a supported file into an upright RGB image."""
    path_p = pathlib.Path(path)
    ext = path_p.suffix.lower()

    if ext in RAW_EXTS:
        return _decode_raw(path_p, bg_rgb, raw_wb, raw_half_size)

    if ext in HEIF_EXTS:
        require_heif("Failed to decode HEIF/HIF input file.")

    with Image.open(str(path_p)) as opened:
        opened.load()
        img = ImageOps.exif_transpose(opened)

    # exif_transpose strips the orientation tag it just applied.
    metadata = Metadata(
        exif=img.info.get("exif"),
        icc=img.info.get("icc_profile"),
        xmp=img.info.get("xmp") or img.info.get("XML:com.adobe.xmp"),
    )
    return Decoded(flatten_to_rgb(img, bg_rgb), metadata)


def downscale(img: Image.Image, max_edge: int | None) -> Image.Image:
    """Resize so the longer edge is at most `max_edge`, keeping aspect ratio."""
    if max_edge is None:
        return img
    width, height = img.size
    if max(width, height) <= max_edge:
        return img
    scale = max_edge / max(width, height)
    size = (max(1, round(width * scale)), max(1, round(height * scale)))
    return img.resize(size, Image.LANCZOS)


def _jpeg_subsampling(mode: str, quality: int) -> int:
    if mode != "auto":
        return _SUBSAMPLING_CODES[mode]
    # Chroma subsampling would otherwise cap the quality the user paid for.
    if quality >= 92:
        return _SUBSAMPLING_CODES["444"]
    if quality >= 85:
        return _SUBSAMPLING_CODES["422"]
    return _SUBSAMPLING_CODES["420"]


def _encode_jpeg(img: Image.Image, **kwargs: object) -> bytes:
    """
    Encode a JPEG in memory.

    A progressive, optimized JPEG has to fit one whole scan in Pillow's encode
    buffer when the target is not a real file, so the buffer is sized from the
    pixel count and grown on overflow.
    """
    width, height = img.size
    block = max(1 << 20, width * height // 8)
    while True:
        original = ImageFile.MAXBLOCK
        ImageFile.MAXBLOCK = block
        buffer = io.BytesIO()
        try:
            img.save(buffer, format="JPEG", **kwargs)
            return buffer.getvalue()
        except OSError:
            if block >= _MAX_JPEG_BLOCK:
                raise
            block *= 4
        finally:
            ImageFile.MAXBLOCK = original


def _save_heif(img: Image.Image, buffer: io.BytesIO, **kwargs: object) -> None:
    require_heif("Failed to encode HEIF output file.")
    try:
        img.save(buffer, format="HEIF", **kwargs)
        return
    except Exception as exc:
        first_error = exc

    from_pillow = getattr(pillow_heif, "from_pillow", None)
    if callable(from_pillow):
        # Older builds need the HeifFile API, and not all of them accept the
        # metadata keywords, so fall back to a bare encode as a last resort.
        for attempt in (kwargs, {"quality": kwargs.get("quality", 80)}):
            buffer.seek(0)
            buffer.truncate(0)
            try:
                from_pillow(img).save(buffer, **attempt)
                return
            except Exception as exc:
                first_error = exc
    buffer.seek(0)
    buffer.truncate(0)

    raise RuntimeError(
        "HEIF encoding failed with your installed pillow-heif build.\n"
        f"Last error: {first_error!r}\n"
        "Fix options:\n"
        "  - Upgrade: pip install -U pillow-heif\n"
        "  - Install system libheif (macOS: brew install libheif; "
        "Ubuntu: apt-get install libheif1 libheif-dev)\n"
    )


def encode(
    img: Image.Image,
    out_format: str,
    quality: int,
    metadata: Metadata = NO_METADATA,
    subsampling: str = "auto",
) -> bytes:
    """Encode to JPEG or HEIF in memory and return the file bytes."""
    if img.mode != "RGB":
        img = img.convert("RGB")

    for key in _INFO_METADATA_KEYS:
        img.info.pop(key, None)

    kwargs: dict[str, object] = {"quality": quality}
    exif_bytes = exifmeta.normalize_exif(metadata.exif, img.size)
    if exif_bytes:
        kwargs["exif"] = exif_bytes
    if metadata.icc:
        kwargs["icc_profile"] = metadata.icc
    if metadata.xmp:
        kwargs["xmp"] = metadata.xmp

    if out_format == "jpg":
        return _encode_jpeg(
            img,
            optimize=True,
            progressive=True,
            subsampling=_jpeg_subsampling(subsampling, quality),
            **kwargs,
        )

    buffer = io.BytesIO()
    _save_heif(img, buffer, **kwargs)
    return buffer.getvalue()


def quality_cap(out_format: str) -> int:
    """Highest sensible quality value for a format."""
    return 95 if out_format == "jpg" else 100


def _best_quality_under(
    img: Image.Image,
    out_format: str,
    max_bytes: int,
    quality_max: int,
    metadata: Metadata,
    subsampling: str,
) -> bytes | None:
    """Binary-search the highest quality whose encoding fits in `max_bytes`."""
    best: bytes | None = None
    low, high = 1, quality_max
    while low <= high:
        mid = (low + high) // 2
        payload = encode(img, out_format, mid, metadata, subsampling)
        if len(payload) <= max_bytes:
            best = payload
            low = mid + 1
        else:
            high = mid - 1
    return best


def fit_under_max_bytes(
    img: Image.Image,
    out_format: str,
    max_bytes: int,
    quality_max: int,
    metadata: Metadata = NO_METADATA,
    subsampling: str = "auto",
) -> bytes:
    """
    Return the best encoding that fits in `max_bytes`.

    Quality is lowered first so pixels are kept; only when even the lowest
    quality overshoots is the image downscaled. Every attempt is encoded in
    memory, and each downscale starts from the full-resolution image so
    repeated resampling cannot soften the result.
    """
    quality_max = min(max(1, quality_max), quality_cap(out_format))
    edge = max(img.size)

    while True:
        candidate = downscale(img, edge)
        payload = _best_quality_under(
            candidate, out_format, max_bytes, quality_max, metadata, subsampling
        )
        if payload is not None:
            return payload

        if edge <= _MIN_FIT_EDGE:
            smallest = len(encode(candidate, out_format, 1, metadata, subsampling))
            raise RuntimeError(
                f"Cannot fit under {max_bytes} bytes: even {candidate.size[0]}x"
                f"{candidate.size[1]} at quality 1 needs {smallest} bytes."
            )
        edge = max(_MIN_FIT_EDGE, int(edge * 0.8))
