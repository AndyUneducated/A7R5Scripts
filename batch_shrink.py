#!/usr/bin/env python3
"""
Batch shrink A7R V files (JPG/PNG/HEIC/HEIF/HIF/ARW) to HEIF or JPEG.

Behaviour worth knowing:
- The input tree is mirrored into the output directory.
- EXIF Orientation is baked into the pixels, and EXIF/ICC are carried over
  (RAW metadata is recovered from the embedded preview).
- Transparency is composited onto a background colour.
- --max-size searches for the best encoding entirely in memory.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import sys
from collections.abc import Iterable, Sequence
from concurrent.futures import ProcessPoolExecutor, as_completed
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass

from a7r5 import fsutil, imaging, progress

OUT_EXTENSIONS = {"heif": ".heic", "jpg": ".jpg"}
DEFAULT_MAX_EDGE = 6000
DEFAULT_QUALITY = 80
MAX_DEFAULT_WORKERS = 8

STATUS_OK = "ok"
STATUS_SKIPPED = "skipped"
STATUS_ERROR = "error"


@dataclass(frozen=True)
class Options:
    """Everything a worker needs to convert one file."""

    out_format: str
    max_edge: int | None
    quality: int
    max_bytes: int | None
    strip: bool
    bg_rgb: tuple[int, int, int]
    raw_wb: str
    raw_half_size: bool
    subsampling: str
    overwrite: bool


@dataclass(frozen=True)
class Result:
    src: str
    dst: str
    before: int
    after: int
    status: str
    error: str | None = None


def _write_atomically(path: pathlib.Path, payload: bytes) -> None:
    """Write via a temporary file so an interrupt cannot leave a partial image."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    try:
        tmp.write_bytes(payload)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def process_one(src: str, dst: str, opts: Options) -> Result:
    """Convert a single file. Never raises; failures come back as a Result."""
    src_p = pathlib.Path(src)
    dst_p = pathlib.Path(dst)

    if not opts.overwrite and dst_p.exists():
        return Result(src, dst, 0, 0, STATUS_SKIPPED)

    try:
        before = src_p.stat().st_size
        decoded = imaging.decode(
            src_p,
            bg_rgb=opts.bg_rgb,
            raw_wb=opts.raw_wb,
            raw_half_size=opts.raw_half_size,
        )
        img = imaging.downscale(decoded.image, opts.max_edge)
        metadata = imaging.NO_METADATA if opts.strip else decoded.metadata

        if opts.max_bytes is None:
            payload = imaging.encode(
                img, opts.out_format, opts.quality, metadata, opts.subsampling
            )
        else:
            payload = imaging.fit_under_max_bytes(
                img,
                opts.out_format,
                opts.max_bytes,
                opts.quality,
                metadata,
                opts.subsampling,
            )

        if not payload:
            raise RuntimeError("Encoder produced no data.")

        _write_atomically(dst_p, payload)
        return Result(src, dst, before, len(payload), STATUS_OK)
    except Exception as exc:
        return Result(src, dst, 0, 0, STATUS_ERROR, f"{src} -> {exc!r}")


def output_name(src: pathlib.Path, out_ext: str, naming: str) -> str:
    """
    Build the output filename.

    The default keeps the source extension in the name so that `DSC1.ARW` and
    `DSC1.JPG` in one folder cannot land on the same output file.
    """
    if naming == "plain":
        return f"{src.stem}{out_ext}"
    return f"{src.stem}.{src.suffix.lower().lstrip('.')}{out_ext}"


def build_plan(
    inputs: Iterable[pathlib.Path],
    in_root: pathlib.Path,
    out_root: pathlib.Path,
    out_ext: str,
    naming: str,
) -> tuple[list[tuple[pathlib.Path, pathlib.Path]], list[str]]:
    """Pair every input with a unique output path inside the mirrored tree."""
    plan: list[tuple[pathlib.Path, pathlib.Path]] = []
    taken: dict[str, pathlib.Path] = {}
    warnings: list[str] = []

    for src in inputs:
        dst = fsutil.mirror_path(
            src, in_root, out_root, output_name(src, out_ext, naming)
        )
        key = str(dst).lower()
        if key in taken:
            alt = fsutil.mirror_path(
                src, in_root, out_root, output_name(src, out_ext, "source-ext")
            )
            if str(alt).lower() in taken:
                index = 2
                while str(
                    alt.with_name(f"{alt.stem}_{index}{alt.suffix}")
                ).lower() in taken:
                    index += 1
                alt = alt.with_name(f"{alt.stem}_{index}{alt.suffix}")
            warnings.append(
                f"{src} would collide with {taken[key]}; writing {alt.name} instead"
            )
            dst = alt
            key = str(dst).lower()

        taken[key] = src
        plan.append((src, dst))

    return plan, warnings


def parse_bg(text: str) -> tuple[int, int, int]:
    """Parse a background colour: `white`, `black` or `R,G,B`."""
    lowered = text.strip().lower()
    if lowered == "white":
        return (255, 255, 255)
    if lowered == "black":
        return (0, 0, 0)

    parts = [p.strip() for p in text.split(",")]
    if len(parts) == 3:
        try:
            values = [int(p) for p in parts]
        except ValueError:
            values = []
        if values and all(0 <= v <= 255 for v in values):
            return (values[0], values[1], values[2])

    raise ValueError(
        f"Invalid --bg {text!r}. Use white, black or 'R,G,B' (e.g. 255,255,255)."
    )


def _run(
    plan: Sequence[tuple[pathlib.Path, pathlib.Path]],
    opts: Options,
    workers: int,
) -> tuple[list[Result], bool]:
    """Execute the plan. Returns the results and whether it was interrupted."""
    results: list[Result] = []

    if workers == 1:
        try:
            for src, dst in progress.iterate(plan, len(plan), "Processing"):
                results.append(process_one(str(src), str(dst), opts))
        except KeyboardInterrupt:
            return results, True
        return results, False

    try:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = [
                pool.submit(process_one, str(src), str(dst), opts) for src, dst in plan
            ]
            for future in progress.iterate(
                as_completed(futures), len(futures), "Processing"
            ):
                results.append(future.result())
    except KeyboardInterrupt:
        return results, True
    except BrokenProcessPool:
        print(
            "\nERROR: a worker process died, most likely out of memory.\n"
            "A single A7R V RAW needs roughly 200 MB per worker; "
            "retry with fewer --workers.",
            file=sys.stderr,
        )
        return results, True

    return results, False


def _report(
    results: Sequence[Result],
    total_planned: int,
    out_format: str,
    interrupted: bool,
) -> None:
    done = [r for r in results if r.status == STATUS_OK]
    skipped = [r for r in results if r.status == STATUS_SKIPPED]
    failed = [r for r in results if r.status == STATUS_ERROR]

    if failed:
        print("\n--- Errors ---")
        for result in failed[:50]:
            print(f"ERROR: {result.error}")
        if len(failed) > 50:
            print(f"... and {len(failed) - 50} more")

    before = sum(r.before for r in done)
    after = sum(r.after for r in done)

    print()
    if interrupted:
        print(f"Interrupted after {len(results)}/{total_planned} files")
    print(f"Converted    : {len(done)}")
    print(f"Skipped      : {len(skipped)} (already existed; use --overwrite)")
    print(f"Failed       : {len(failed)}")
    if done:
        print(f"Total before : {fsutil.human_bytes(before)}")
        print(f"Total after  : {fsutil.human_bytes(after)}")
        if before:
            print(f"Reduction    : {100 - after / before * 100:.1f}%")
    print(f"Output format: {out_format}")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Batch shrink A7R V files (JPG/PNG/HEIC/HEIF/HIF/ARW) to HEIF or JPEG."
        )
    )
    parser.add_argument("in_dir", help="Input directory (scanned recursively)")
    parser.add_argument("out_dir", help="Output directory (input tree is mirrored)")

    parser.add_argument(
        "--out-format",
        choices=["heif", "jpg"],
        default="heif",
        help="Output format (default: heif)",
    )
    parser.add_argument(
        "--max-edge",
        type=int,
        default=None,
        help=(
            f"Max long edge in pixels. Default: {DEFAULT_MAX_EDGE}, "
            "or unlimited when --max-size is set"
        ),
    )
    parser.add_argument(
        "--quality",
        type=int,
        default=None,
        help=(
            "Quality 1-95 for JPEG, 1-100 for HEIF. "
            f"Default: {DEFAULT_QUALITY}, or the maximum when --max-size is set"
        ),
    )
    parser.add_argument(
        "--max-size",
        default=None,
        help="Max output file size (e.g. 3mb). Picks the best encoding that fits.",
    )
    parser.add_argument(
        "--subsampling",
        choices=list(imaging.SUBSAMPLING_MODES),
        default="auto",
        help="(JPEG only) Chroma subsampling. auto: 4:4:4/4:2:2/4:2:0 by quality",
    )
    parser.add_argument(
        "--raw-wb",
        choices=list(imaging.RAW_WB_MODES),
        default="camera",
        help="(RAW only) White balance: camera (default), auto or none",
    )
    parser.add_argument(
        "--raw-half-size",
        action="store_true",
        help="(RAW only) Decode at half resolution; much faster for small outputs",
    )
    parser.add_argument(
        "--strip",
        action="store_true",
        help="Write no EXIF and no ICC profile",
    )
    parser.add_argument(
        "--naming",
        choices=["source-ext", "plain"],
        default="source-ext",
        help=(
            "Output naming: source-ext keeps the source extension "
            "(DSC1.arw.heic), plain does not (DSC1.heic)"
        ),
    )
    parser.add_argument(
        "--bg",
        default="white",
        help="Background for transparent images: white / black / 'R,G,B'",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=min(os.cpu_count() or 4, MAX_DEFAULT_WORKERS),
        help=f"Parallel workers. Default: CPU count capped at {MAX_DEFAULT_WORKERS}",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing outputs instead of skipping them",
    )
    parser.add_argument(
        "--keep-orientation-only",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    return parser.parse_args(argv)


def _resolve_settings(args: argparse.Namespace) -> tuple[Options, int]:
    """Validate CLI arguments and turn them into worker Options."""
    errors: list[str] = []

    max_bytes: int | None = None
    if args.max_size:
        try:
            max_bytes = fsutil.parse_size(args.max_size)
        except ValueError as exc:
            errors.append(f"--max-size: {exc}")

    max_edge = args.max_edge
    if max_edge is None and max_bytes is None:
        max_edge = DEFAULT_MAX_EDGE
    if max_edge is not None and max_edge <= 0:
        errors.append("--max-edge must be > 0")

    cap = imaging.quality_cap(args.out_format)
    quality = args.quality
    if quality is None:
        quality = cap if max_bytes is not None else DEFAULT_QUALITY
    if not 1 <= quality <= cap:
        errors.append(f"For {args.out_format}, --quality must be between 1 and {cap}")

    if args.workers <= 0:
        errors.append("--workers must be > 0")

    bg_rgb = (255, 255, 255)
    try:
        bg_rgb = parse_bg(args.bg)
    except ValueError as exc:
        errors.append(str(exc))

    if errors:
        for message in errors:
            print(f"ERROR: {message}", file=sys.stderr)
        sys.exit(2)

    return (
        Options(
            out_format=args.out_format,
            max_edge=max_edge,
            quality=quality,
            max_bytes=max_bytes,
            strip=args.strip,
            bg_rgb=bg_rgb,
            raw_wb=args.raw_wb,
            raw_half_size=args.raw_half_size,
            subsampling=args.subsampling,
            overwrite=args.overwrite,
        ),
        args.workers,
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)

    if args.keep_orientation_only:
        print(
            "NOTE: --keep-orientation-only is obsolete and ignored. Orientation is "
            "baked into the pixels and the tag is removed from the written EXIF.",
            file=sys.stderr,
        )

    opts, workers = _resolve_settings(args)

    if opts.out_format == "heif":
        try:
            imaging.require_heif(
                "You selected --out-format heif but HEIF support is unavailable."
            )
        except RuntimeError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 3

    in_root = pathlib.Path(args.in_dir)
    out_root = pathlib.Path(args.out_dir)
    if not in_root.is_dir():
        print(f"ERROR: input directory not found: {in_root}", file=sys.stderr)
        return 2

    inputs = list(
        fsutil.iter_files(in_root, imaging.SUPPORTED_IN, exclude_dirs=[out_root])
    )
    if not inputs:
        extensions = " ".join(sorted(e.lstrip(".") for e in imaging.SUPPORTED_IN))
        print(f"No supported files found in {in_root} ({extensions}).")
        return 1

    plan, warnings = build_plan(
        inputs, in_root, out_root, OUT_EXTENSIONS[opts.out_format], args.naming
    )
    for warning in warnings:
        print(f"WARNING: {warning}", file=sys.stderr)

    results, interrupted = _run(plan, opts, workers)
    _report(results, len(plan), opts.out_format, interrupted)

    if interrupted:
        return 130
    if any(r.status == STATUS_ERROR for r in results):
        return 10
    return 0


if __name__ == "__main__":
    sys.exit(main())
