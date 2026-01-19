#!/usr/bin/env python3
"""
Batch shrink A7R V files (JPG/PNG/HEIC/ARW) to smaller JPEGs.

Fixes:
- ✅ Always keeps correct orientation by "baking" EXIF Orientation into pixels (ImageOps.exif_transpose)
- ✅ Handles alpha images (PNG/HEIC with transparency) by compositing onto a background (default white)
- ✅ If you want: keep ONLY Orientation EXIF tag (requires piexif) via --keep-orientation-only
  - Otherwise, you can just use --strip to remove all EXIF (orientation still correct because it's baked)
"""

import argparse, sys, os, pathlib
from concurrent.futures import ProcessPoolExecutor, as_completed

from PIL import Image, ImageOps
import rawpy  # for .ARW
try:
    import pillow_heif  # optional, for HEIC/HEIF
    pillow_heif.register_heif_opener()
except Exception:
    pass

SUPPORTED_IN = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".arw"}


def _composite_alpha_to_rgb(img: Image.Image, bg_rgb=(255, 255, 255)) -> Image.Image:
    """
    如果图片带 alpha（RGBA/LA/带透明的P），保存 JPEG 会出现“黑底/怪底色”问题。
    这里把透明合成到指定背景色（默认白色）后再转 RGB。
    """
    if img.mode in ("RGBA", "LA"):
        bg = Image.new("RGBA", img.size, bg_rgb + (255,))
        return Image.alpha_composite(bg, img.convert("RGBA")).convert("RGB")

    # P 模式可能带透明调色板
    if img.mode == "P":
        if "transparency" in img.info:
            return _composite_alpha_to_rgb(img.convert("RGBA"), bg_rgb=bg_rgb)
        return img.convert("RGB")

    # CMYK 等其它模式
    if img.mode not in ("RGB", "L"):
        return img.convert("RGB")

    # L（灰度）也能直接存 JPEG，这里不强转，保持原样
    return img


def decode_image(path: pathlib.Path, bg_rgb=(255, 255, 255)) -> Image.Image:
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
        # ARW 这一步一般没有 EXIF Orientation（且 rawpy 已经输出正确方向）
        return _composite_alpha_to_rgb(img, bg_rgb=bg_rgb)

    img = Image.open(str(path))

    # ✅ 关键：把 EXIF Orientation 直接“烘焙”到像素上
    # 这样即使后面把 EXIF 全剥离，方向也永远正确
    img = ImageOps.exif_transpose(img)

    img = _composite_alpha_to_rgb(img, bg_rgb=bg_rgb)
    return img


def downscale(img: Image.Image, max_edge: int) -> Image.Image:
    w, h = img.size
    if max(w, h) <= max_edge:
        return img
    scale = max_edge / max(w, h)
    new_size = (max(1, int(w * scale)), max(1, int(h * scale)))
    return img.resize(new_size, Image.LANCZOS)


def _keep_only_orientation_exif(exif_bytes: bytes | None) -> bytes | None:
    """
    仅保留 EXIF Orientation(0x0112)。
    需要安装：pip install piexif
    """
    if not exif_bytes:
        return None
    try:
        import piexif
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
    except Exception:
        return None


def process_one(
    in_path: str,
    out_dir: str,
    max_edge: int,
    quality: int,
    strip: bool,
    overwrite: bool,
    keep_orientation_only: bool,
    bg_rgb: tuple[int, int, int],
) -> tuple[str, int, int]:
    in_p = pathlib.Path(in_path)
    out_base = in_p.with_suffix(".jpg").name
    out_p = pathlib.Path(out_dir) / out_base
    if (not overwrite) and out_p.exists():
        return (in_path, 0, 0)

    try:
        img = decode_image(in_p, bg_rgb=bg_rgb)
        img = downscale(img, max_edge)

        save_kwargs = {
            "format": "JPEG",
            "quality": quality,
            "optimize": True,
            "progressive": True,
            "subsampling": 2,  # 4:2:0
        }

        # ✅ ICC profile 建议保留（不算 EXIF，影响色彩一致性）
        icc = img.info.get("icc_profile", None)
        if icc and (not strip):
            save_kwargs["icc_profile"] = icc

        # EXIF 处理策略：
        # 1) 默认：strip=True -> 不写任何 EXIF（推荐）
        #    方向不会错，因为已经 exif_transpose 烘焙到像素了
        # 2) strip=False & keep_orientation_only=True -> 仅写 Orientation（需要 piexif）
        # 3) strip=False & keep_orientation_only=False -> 原样保留 EXIF（不推荐你现在的目标）
        exif_bytes = img.info.get("exif", None)
        if strip:
            pass
        else:
            if keep_orientation_only:
                only_o = _keep_only_orientation_exif(exif_bytes)
                if only_o:
                    save_kwargs["exif"] = only_o
            else:
                if exif_bytes:
                    save_kwargs["exif"] = exif_bytes

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
    ap.add_argument("--strip", action="store_true", help="Strip ALL EXIF metadata (orientation still correct)")
    ap.add_argument("--keep-orientation-only", action="store_true",
                    help="Keep ONLY EXIF Orientation tag (requires piexif). Ignored if --strip is set.")
    ap.add_argument("--bg", default="white",
                    help="Background for transparent images: white/black or 'R,G,B' (default white)")
    ap.add_argument("--workers", type=int, default=os.cpu_count() or 4, help="Parallel workers")
    ap.add_argument("--overwrite", action="store_true", help="Overwrite same-named outputs")
    args = ap.parse_args()

    inputs = list(walk_inputs(args.in_dir))
    if not inputs:
        print("No supported files found (jpg/jpeg/png/heic/heif/arw).")
        sys.exit(1)

    # parse bg
    if args.bg.lower() == "white":
        bg_rgb = (255, 255, 255)
    elif args.bg.lower() == "black":
        bg_rgb = (0, 0, 0)
    else:
        try:
            parts = [int(x.strip()) for x in args.bg.split(",")]
            if len(parts) != 3 or any(not (0 <= v <= 255) for v in parts):
                raise ValueError
            bg_rgb = (parts[0], parts[1], parts[2])
        except Exception:
            print("Invalid --bg. Use white/black or 'R,G,B' (e.g., 255,255,255).")
            sys.exit(2)

    from tqdm import tqdm
    before_sum = after_sum = 0
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        futs = [
            ex.submit(
                process_one,
                p,
                args.out_dir,
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
