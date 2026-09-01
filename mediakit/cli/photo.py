"""`mediakit photo shrink` — batch-compress photos to HEIF or JPEG."""

from __future__ import annotations

import argparse
import os
import pathlib
import sys

from mediakit.core import fsutil
from mediakit.core.exitcodes import INTERRUPTED, MISSING_DEP, NO_INPUT, OK, PARTIAL_FAIL, USAGE
from mediakit.core.pipeline import ConsoleReporter
from mediakit.photo import imaging
from mediakit.photo.shrink import (
    DEFAULT_MAX_EDGE,
    DEFAULT_QUALITY,
    MAX_DEFAULT_WORKERS,
    OUT_EXTENSIONS,
    STATUS_ERROR,
    Options,
    ShrinkPlan,
    build_pairs,
    parse_bg,
    summarize,
)


def register(parser: argparse.ArgumentParser) -> None:
    sub = parser.add_subparsers(dest="photo_command", required=True)
    p = sub.add_parser(
        "shrink",
        help="批量压缩照片（镜像目录，保留 EXIF）",
        description=(
            "递归扫描输入目录，压缩为 HEIF 或 JPEG，镜像写出。"
            " Orientation 烘进像素；RAW 的 EXIF 取自内嵌预览。"
        ),
    )
    p.add_argument("in_dir", help="输入目录（递归扫描）")
    p.add_argument("out_dir", help="输出目录（镜像输入结构）")
    p.add_argument(
        "--out-format",
        choices=["heif", "jpg"],
        default="heif",
        help="输出格式（默认 heif）",
    )
    p.add_argument(
        "--max-edge",
        type=int,
        default=None,
        help=(
            f"最长边像素。默认 {DEFAULT_MAX_EDGE}；"
            "设了 --max-size 时默认不限制"
        ),
    )
    p.add_argument(
        "--quality",
        type=int,
        default=None,
        help=(
            "质量：JPEG 1–95，HEIF 1–100。"
            f"默认 {DEFAULT_QUALITY}；设了 --max-size 时取上限"
        ),
    )
    p.add_argument(
        "--max-size",
        default=None,
        help="输出体积上限（如 3mb）。在内存中搜索能放进该体积的最佳编码",
    )
    p.add_argument(
        "--subsampling",
        choices=list(imaging.SUBSAMPLING_MODES),
        default="auto",
        help="（仅 JPEG）色度抽样。auto：按质量选 4:4:4 / 4:2:2 / 4:2:0",
    )
    p.add_argument(
        "--raw-wb",
        choices=list(imaging.RAW_WB_MODES),
        default="camera",
        help="（仅 RAW）白平衡：camera（默认）/ auto / none",
    )
    p.add_argument(
        "--raw-half-size",
        action="store_true",
        help="（仅 RAW）半分辨率解码，小尺寸输出时更快",
    )
    p.add_argument("--strip", action="store_true", help="不写 EXIF 与 ICC")
    p.add_argument(
        "--naming",
        choices=["source-ext", "plain"],
        default="source-ext",
        help="source-ext 保留源扩展名（DSC1.arw.heic）；plain 不保留（DSC1.heic）",
    )
    p.add_argument(
        "--bg",
        default="white",
        help="透明图背景：white / black / 'R,G,B'",
    )
    p.add_argument(
        "--workers",
        type=int,
        default=min(os.cpu_count() or 4, MAX_DEFAULT_WORKERS),
        help=f"并行进程数。默认 CPU 核数（上限 {MAX_DEFAULT_WORKERS}）",
    )
    p.add_argument("--overwrite", action="store_true", help="覆盖已存在的输出")
    p.add_argument("--dry-run", action="store_true", help="只打印计划，不写文件")
    p.add_argument(
        "--keep-orientation-only",
        action="store_true",
        help=argparse.SUPPRESS,
    )


def _resolve_options(args: argparse.Namespace) -> Options:
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
        raise SystemExit(USAGE)

    return Options(
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
    )


def run(args: argparse.Namespace) -> int:
    if args.photo_command != "shrink":
        print(f"未知命令：photo {args.photo_command}", file=sys.stderr)
        return USAGE

    if args.keep_orientation_only:
        print(
            "NOTE: --keep-orientation-only is obsolete and ignored. Orientation is "
            "baked into the pixels and the tag is removed from the written EXIF.",
            file=sys.stderr,
        )

    opts = _resolve_options(args)

    if opts.out_format == "heif":
        try:
            imaging.require_heif(
                "You selected --out-format heif but HEIF support is unavailable."
            )
        except RuntimeError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return MISSING_DEP

    in_root = pathlib.Path(args.in_dir)
    out_root = pathlib.Path(args.out_dir)
    if not in_root.is_dir():
        print(f"ERROR: input directory not found: {in_root}", file=sys.stderr)
        return USAGE

    inputs = list(
        fsutil.iter_files(in_root, imaging.SUPPORTED_IN, exclude_dirs=[out_root])
    )
    if not inputs:
        extensions = " ".join(sorted(e.lstrip(".") for e in imaging.SUPPORTED_IN))
        print(f"No supported files found in {in_root} ({extensions}).")
        return NO_INPUT

    pairs, warnings = build_pairs(
        inputs, in_root, out_root, OUT_EXTENSIONS[opts.out_format], args.naming
    )
    plan = ShrinkPlan(pairs=pairs, options=opts, workers=args.workers, warnings=warnings)
    print(plan.describe())
    for warning in plan.warnings:
        print(f"WARNING: {warning}", file=sys.stderr)

    plan.execute(dry_run=args.dry_run, reporter=ConsoleReporter())
    if args.dry_run:
        print("（dry-run，未实际执行）")
        return OK

    print()
    print(summarize(plan))
    if plan.interrupted:
        return INTERRUPTED
    if any(r.status == STATUS_ERROR for r in plan.results):
        return PARTIAL_FAIL
    return OK
