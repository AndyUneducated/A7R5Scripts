#!/usr/bin/env python3
"""
Batch shrink A7R V files (JPG/PNG/HEIC/ARW) to smaller JPEGs.

- Downscales to a max long-edge (default 6000 px).
- Sets JPEG quality (default 80) and 4:2:0 chroma subsampling.
- Optionally strips metadata.
- Uses multi-processing for speed.
"""

import argparse, sys, os, pathlib, io
from concurrent.futures import ProcessPoolExecutor, as_completed

from PIL import Image, ImageOps
import rawpy  # for .ARW
try:
    import pillow_heif  # optional, for HEIC/HEIF
    pillow_heif.register_heif_opener()
except Exception:
    pass

SUPPORTED_IN = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".arw"}

def decode_image(path: pathlib.Path):
    ext = path.suffix.lower()
    if ext == ".arw":
        with rawpy.imread(str(path)) as raw:
            # Good defaults: daylight-accurate WB, no auto-bright, high quality demosaic
            rgb = raw.postprocess(use_auto_wb=True, no_auto_bright=True, output_bps=8, gamma=(2.222, 4.5))
        img = Image.fromarray(rgb)
        return img
    else:
        img = Image.open(str(path))
        # Ensure RGB for JPEG
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        return img

def downscale(img: Image.Image, max_edge: int) -> Image.Image:
    w, h = img.size
    if max(w, h) <= max_edge:
        return img
    scale = max_edge / max(w, h)
    new_size = (int(w * scale), int(h * scale))
    # Lanczos for quality
    return img.resize(new_size, Image.LANCZOS)

def process_one(in_path: str, out_dir: str, max_edge: int, quality: int, strip: bool, overwrite: bool) -> tuple[str, int, int]:
    in_p = pathlib.Path(in_path)
    out_base = in_p.with_suffix(".jpg").name
    out_p = pathlib.Path(out_dir) / out_base
    if (not overwrite) and out_p.exists():
        return (in_path, 0, 0)

    try:
        img = decode_image(in_p)
        img = downscale(img, max_edge)

        save_kwargs = {
            "format": "JPEG",
            "quality": quality,
            "optimize": True,
            "progressive": True,   # smaller + web-friendly
            "subsampling": 2,      # 4:2:0 (2=4:2:0, 1=4:2:2, 0=4:4:4)
        }
        # Preserve ICC profile if present (helps color accuracy)
        icc = img.info.get("icc_profile", None)
        if icc and not strip:
            save_kwargs["icc_profile"] = icc

        # Strip EXIF by default if strip=True
        if strip:
            exif_bytes = None
        else:
            exif_bytes = img.info.get("exif", None)
            if exif_bytes:
                save_kwargs["exif"] = exif_bytes

        # Write to bytes first to compare sizes if needed
        os.makedirs(out_dir, exist_ok=True)
        before = os.path.getsize(in_p) if in_p.exists() else 0
        img.save(out_p, **save_kwargs)
        after = os.path.getsize(out_p) if out_p.exists() else 0
        return (in_path, before, after)
    except Exception as e:
        return (f"ERROR: {in_path} -> {e}", 0, 0)

def walk_inputs(in_dir: str):
    for root, _, files in os.walk(in_dir):
        for f in files:
            p = pathlib.Path(root) / f
            if p.suffix.lower() in SUPPORTED_IN:
                yield str(p)

def main():
    ap = argparse.ArgumentParser(description="Batch shrink A7R V files (JPG/PNG/HEIC/ARW) to JPEG.")
    ap.add_argument("in_dir", help="Input directory")
    ap.add_argument("out_dir", help="Output directory")
    ap.add_argument("--max-edge", type=int, default=6000, help="Max long edge (pixels), default 6000")
    ap.add_argument("--quality", type=int, default=80, help="JPEG quality 1–95, default 80")
    ap.add_argument("--strip", action="store_true", help="Strip metadata/EXIF")
    ap.add_argument("--workers", type=int, default=os.cpu_count() or 4, help="Parallel workers")
    ap.add_argument("--overwrite", action="store_true", help="Overwrite same-named outputs")
    args = ap.parse_args()

    inputs = list(walk_inputs(args.in_dir))
    if not inputs:
        print("No supported files found (jpg/jpeg/png/heic/heif/arw).")
        sys.exit(1)

    # Parallel
    from tqdm import tqdm
    before_sum = after_sum = 0
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        futs = [ex.submit(process_one, p, args.out_dir, args.max_edge, args.quality, args.strip, args.overwrite) for p in inputs]
        for fut in tqdm(as_completed(futs), total=len(futs), desc="Processing"):
            p, before, after = fut.result()
            before_sum += before
            after_sum += after

    if before_sum > 0:
        ratio = (after_sum / before_sum) * 100
        print(f"Total before: {before_sum/1_000_000:.1f} MB")
        print(f"Total after : {after_sum/1_000_000:.1f} MB")
        print(f"Reduction   : {100 - ratio:.1f}%")

if __name__ == "__main__":
    main()
