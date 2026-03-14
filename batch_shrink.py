#!/usr/bin/env python3
"""
Batch shrink A7R V files (JPG/PNG/HEIC/HEIF/HIF/ARW) to JPEG or HEIF.

Optimizations included:
- Supports: JPG / JPEG / PNG / HEIC / HEIF / HIF / HIFC / ARW
- Always keeps correct orientation by baking EXIF Orientation into pixels
- Handles transparency by compositing onto a background color
- Avoids Pillow lazy-loading file handle issues
- Normalizes non-RGB images to RGB before saving
- Avoids output name collisions across different source extensions
- Validates HEIF support up front when needed
- Optional EXIF strategy for JPEG output
- Clear dependency / runtime errors
"""

import argparse
import os
import pathlib
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed

from PIL import Image, ImageOps
import rawpy  # for .ARW

# --- HEIF/HEIC/HIF support ---
HEIF_ENABLED = False
HEIF_IMPORT_ERROR: str | None = None
try:
    import pillow_heif  # for HEIC/HEIF/HIF read & HEIF write
    pillow_heif.register_heif_opener()
    HEIF_ENABLED = True
except Exception as e:
    HEIF_ENABLED = False
    HEIF_IMPORT_ERROR = repr(e)

SUPPORTED_IN = {
    ".jpg", ".jpeg", ".png",
    ".heic", ".heif",
    ".hif", ".hifc",  # Sony HIF
    ".arw",
}

HEIF_EXTS = {".heic", ".heif", ".hif", ".hifc"}


def _composite_alpha_to_rgb(img: Image.Image, bg_rgb=(255, 255, 255)) -> Image.Image:
    """
    If image contains alpha, composite it onto a solid background and return RGB.
    Also normalize all non-RGB modes to RGB.
    """
    if img.mode in ("RGBA", "LA"):
        bg = Image.new("RGBA", img.size, bg_rgb + (255,))
        return Image.alpha_composite(bg, img.convert("RGBA")).convert("RGB")

    if img.mode == "P":
        if "transparency" in img.info:
            return _composite_alpha_to_rgb(img.convert("RGBA"), bg_rgb=bg_rgb)
        return img.convert("RGB")

    if img.mode != "RGB":
        return img.convert("RGB")

    return img


def _require_heif(reason: str) -> None:
    """
    Strong validation for scenarios requiring HEIF capability
    (decode HEIF/HIF input or write HEIF output).
    """
    if HEIF_ENABLED:
        return
    msg = (
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
        msg += f"\nImport error: {HEIF_IMPORT_ERROR}\n"
    raise RuntimeError(msg)


def decode_image(path: pathlib.Path, bg_rgb=(255, 255, 255)) -> Image.Image:
    """
    Decode supported input into a Pillow RGB image with orientation already baked in.
    """
    ext = path.suffix.lower()

    if ext == ".arw":
        with rawpy.imread(str(path)) as raw:
            rgb = raw.postprocess(
                use_auto_wb=True,
                no_auto_bright=True,
                output_bps=8,
                gamma=(2.222, 4.5),
            )
        img = Image.fromarray(rgb)
        return _composite_alpha_to_rgb(img, bg_rgb=bg_rgb)

    if ext in HEIF_EXTS and not HEIF_ENABLED:
        _require_heif("Failed to decode HEIF/HIF input file.")

    # Avoid Pillow lazy-loading file handle issues by copying image data immediately.
    with Image.open(str(path)) as im:
        img = im.copy()

    # Bake EXIF orientation into pixels.
    img = ImageOps.exif_transpose(img)
    img = _composite_alpha_to_rgb(img, bg_rgb=bg_rgb)
    return img


def downscale(img: Image.Image, max_edge: int) -> Image.Image:
    """
    Resize image so the longer edge is <= max_edge, keeping aspect ratio.
    """
    w, h = img.size
    if max(w, h) <= max_edge:
        return img
    scale = max_edge / max(w, h)
    new_size = (max(1, int(w * scale)), max(1, int(h * scale)))
    return img.resize(new_size, Image.LANCZOS)


def _keep_only_orientation_exif(exif_bytes: bytes | None) -> bytes | None:
    """
    Keep only EXIF Orientation (0x0112). Requires: pip install piexif
    Note: after exif_transpose(), orientation is usually no longer necessary.
    This option exists only for compatibility workflows.
    """
    if not exif_bytes:
        return None
    try:
        import piexif
    except Exception as e:
        raise RuntimeError(
            f"--keep-orientation-only requires piexif. Install: pip install piexif. Import error: {repr(e)}"
        )

    exif_dict = piexif.load(exif_bytes)
    orient = exif_dict.get("0th", {}).get(piexif.ImageIFD.Orientation, None)
    if orient is None:
        return None
    new_exif = {
        "0th": {piexif.ImageIFD.Orientation: orient},
        "Exif": {},
        "GPS": {},
        "1st": {},
        "thumbnail": None,
    }
    return piexif.dump(new_exif)


def _save_as_jpeg(
    img: Image.Image,
    out_p: pathlib.Path,
    quality: int,
    strip: bool,
    keep_orientation_only: bool,
) -> None:
    """
    Save as JPEG.
    """
    if img.mode != "RGB":
        img = img.convert("RGB")

    save_kwargs = {
        "format": "JPEG",
        "quality": quality,
        "optimize": True,
        "progressive": True,
        "subsampling": 2,  # 4:2:0
    }

    icc = img.info.get("icc_profile", None)
    if icc and (not strip):
        save_kwargs["icc_profile"] = icc

    exif_bytes = img.info.get("exif", None)
    if not strip:
        if keep_orientation_only:
            only_o = _keep_only_orientation_exif(exif_bytes)
            if only_o:
                save_kwargs["exif"] = only_o
        else:
            if exif_bytes:
                save_kwargs["exif"] = exif_bytes

    img.save(out_p, **save_kwargs)


def _save_as_heif(img: Image.Image, out_p: pathlib.Path, quality: int) -> None:
    """
    Save as HEIF/HEIC.
    """
    _require_heif("Failed to encode HEIF output file.")

    if img.mode != "RGB":
        img = img.convert("RGB")

    # Try standard PIL save first; pillow-heif registers writer support.
    last_err = None
    for fmt in ("HEIF", "HEIC"):
        try:
            img.save(str(out_p), format=fmt, quality=quality)
            return
        except Exception as e:
            last_err = e

    # Compatibility fallback for some pillow-heif versions.
    try:
        heif_obj = getattr(pillow_heif, "from_pillow", None)
        if callable(heif_obj):
            hi = heif_obj(img)
            hi.save(str(out_p), quality=quality)
            return
    except Exception as e:
        last_err = e

    raise RuntimeError(
        "HEIF encoding is not supported by your installed pillow-heif build.\n"
        "Tried: PIL.Image.save(format=HEIF/HEIC) and pillow_heif.from_pillow(img).\n"
        f"Last error: {repr(last_err)}\n"
        "Fix options:\n"
        "  - Upgrade: pip install -U pillow-heif\n"
        "  - Ensure system libheif is installed (macOS: brew install libheif; Ubuntu: apt-get install libheif1 libheif-dev)\n"
    )


def _build_output_name(in_p: pathlib.Path, out_format: str) -> str:
    """
    Build output filename while avoiding collisions between files like:
      IMG_0001.JPG
      IMG_0001.ARW

    Output examples:
      IMG_0001.jpg.jpg
      IMG_0001.arw.jpg
      IMG_0001.hif.heic
    """
    src_ext = in_p.suffix.lower().lstrip(".")
    dst_ext = ".jpg" if out_format == "jpg" else ".heic"
    return f"{in_p.stem}.{src_ext}{dst_ext}"


def process_one(
    in_path: str,
    out_dir: str,
    out_format: str,  # "heif" or "jpg"
    max_edge: int,
    quality: int,
    strip: bool,
    overwrite: bool,
    keep_orientation_only: bool,
    bg_rgb: tuple[int, int, int],
) -> tuple[str, int, int, str | None]:
    """
    Returns: (in_path, before_bytes, after_bytes, error_msg)
    """
    in_p = pathlib.Path(in_path)
    out_base = _build_output_name(in_p, out_format)
    out_p = pathlib.Path(out_dir) / out_base

    if (not overwrite) and out_p.exists():
        return (in_path, 0, 0, None)

    try:
        img = decode_image(in_p, bg_rgb=bg_rgb)
        img = downscale(img, max_edge)

        os.makedirs(out_dir, exist_ok=True)
        before = os.path.getsize(in_p) if in_p.exists() else 0

        if out_format == "jpg":
            _save_as_jpeg(
                img=img,
                out_p=out_p,
                quality=quality,
                strip=strip,
                keep_orientation_only=keep_orientation_only,
            )
        else:
            # HEIF output: no EXIF write for now; orientation is already baked.
            _save_as_heif(img=img, out_p=out_p, quality=quality)

        after = os.path.getsize(out_p) if out_p.exists() else 0
        if after == 0:
            raise RuntimeError("Output file not created or size is 0.")
        return (in_path, before, after, None)
    except Exception as e:
        return (in_path, 0, 0, f"{in_path} -> {repr(e)}")


def walk_inputs(in_dir: str):
    """
    Recursively yield supported input file paths.
    """
    for root, _, files in os.walk(in_dir):
        for f in files:
            p = pathlib.Path(root) / f
            if p.suffix.lower() in SUPPORTED_IN:
                yield str(p)


def parse_bg(bg: str) -> tuple[int, int, int]:
    """
    Parse background color from:
      - white
      - black
      - R,G,B
    """
    bg_lower = bg.lower()
    if bg_lower == "white":
        return (255, 255, 255)
    if bg_lower == "black":
        return (0, 0, 0)

    try:
        parts = [int(x.strip()) for x in bg.split(",")]
        if len(parts) != 3 or any(not (0 <= v <= 255) for v in parts):
            raise ValueError
        return (parts[0], parts[1], parts[2])
    except Exception:
        raise ValueError("Invalid --bg. Use white/black or 'R,G,B' (e.g., 255,255,255).")


def main():
    ap = argparse.ArgumentParser(
        description="Batch shrink A7R V files (JPG/PNG/HEIC/HEIF/HIF/ARW) to HEIF or JPEG."
    )
    ap.add_argument("in_dir", help="Input directory")
    ap.add_argument("out_dir", help="Output directory")

    ap.add_argument(
        "--out-format",
        choices=["heif", "jpg"],
        default="heif",
        help="Output format: heif or jpg (default: heif).",
    )
    ap.add_argument(
        "--max-edge",
        type=int,
        default=6000,
        help="Max long edge in pixels. Default: 6000",
    )
    ap.add_argument(
        "--quality",
        type=int,
        default=80,
        help="Quality 1–95 for JPEG, 1–100 for HEIF. Default: 80",
    )
    ap.add_argument(
        "--strip",
        action="store_true",
        help="(JPEG only) Strip all EXIF metadata",
    )
    ap.add_argument(
        "--keep-orientation-only",
        action="store_true",
        help="(JPEG only) Keep only EXIF Orientation tag; ignored if --strip is set",
    )
    ap.add_argument(
        "--bg",
        default="white",
        help="Background for transparent images: white / black / 'R,G,B'",
    )
    ap.add_argument(
        "--workers",
        type=int,
        default=os.cpu_count() or 4,
        help="Parallel workers. Default: CPU count",
    )
    ap.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite same-named outputs",
    )
    args = ap.parse_args()

    if args.max_edge <= 0:
        print("ERROR: --max-edge must be > 0")
        sys.exit(2)

    if args.workers <= 0:
        print("ERROR: --workers must be > 0")
        sys.exit(2)

    if args.out_format == "jpg" and not (1 <= args.quality <= 95):
        print("ERROR: For JPEG, --quality must be between 1 and 95")
        sys.exit(2)

    if args.out_format == "heif" and not (1 <= args.quality <= 100):
        print("ERROR: For HEIF, --quality must be between 1 and 100")
        sys.exit(2)

    # If output is HEIF, require encoder availability up front.
    if args.out_format == "heif":
        _require_heif("You selected --out-format heif but HEIF support is unavailable.")

    try:
        bg_rgb = parse_bg(args.bg)
    except ValueError as e:
        print(str(e))
        sys.exit(2)

    inputs = list(walk_inputs(args.in_dir))
    if not inputs:
        print("No supported files found (jpg/jpeg/png/heic/heif/hif/hifc/arw).")
        sys.exit(1)

    # Validate piexif only if needed.
    if args.out_format == "jpg" and (not args.strip) and args.keep_orientation_only:
        try:
            import piexif  # noqa: F401
        except Exception as e:
            print(
                f"ERROR: --keep-orientation-only requires piexif. "
                f"Install: pip install piexif. Import error: {repr(e)}"
            )
            sys.exit(3)

    try:
        from tqdm import tqdm
    except Exception:
        print("ERROR: tqdm is required. Install: pip install tqdm")
        sys.exit(4)

    before_sum = 0
    after_sum = 0
    errors: list[str] = []

    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        futs = [
            ex.submit(
                process_one,
                p,
                args.out_dir,
                args.out_format,
                args.max_edge,
                args.quality,
                args.strip,
                args.overwrite,
                args.keep_orientation_only,
                bg_rgb,
            )
            for p in inputs
        ]

        for fut in tqdm(as_completed(futs), total=len(futs), desc="Processing"):
            p, before, after, err = fut.result()
            if err:
                errors.append(err)
            else:
                before_sum += before
                after_sum += after

    if errors:
        print("\n--- Errors (first 50) ---")
        for e in errors[:50]:
            print(f"ERROR: {e}")
        print(f"Total errors: {len(errors)}")
        sys.exit(10)

    if before_sum > 0:
        ratio = (after_sum / before_sum) * 100
        print(f"Processed files: {len(inputs)}")
        print(f"Total before : {before_sum / 1_000_000:.1f} MB")
        print(f"Total after  : {after_sum / 1_000_000:.1f} MB")
        print(f"Reduction    : {100 - ratio:.1f}%")
        print(f"Output format: {args.out_format}")


if __name__ == "__main__":
    main()