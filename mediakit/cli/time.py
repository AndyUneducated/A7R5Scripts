"""`mediakit time` — shift capture-time metadata without re-encoding pixels."""

from __future__ import annotations

import argparse
import pathlib
import sys

from mediakit.core import fsutil
from mediakit.core.exitcodes import INTERRUPTED, NO_INPUT, OK, PARTIAL_FAIL, USAGE
from mediakit.core.pipeline import ConsoleReporter
from mediakit.time import timeshift
from mediakit.time.fix import DEFAULT_EXTS, FixPlan, require_exiftool


def register(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("-i", "--input", required=True, help="输入目录")
    parser.add_argument(
        "-o",
        "--output",
        default="output",
        help="输出目录（镜像输入结构）。默认 output",
    )
    parser.add_argument(
        "--in-place",
        action="store_true",
        help="直接改输入文件，不先复制",
    )
    parser.add_argument("--shift", help="手动时间偏移，格式 ±HH:MM:SS")
    parser.add_argument(
        "--from-offset", help="相机记录的原始 UTC 偏移，如 -08:00"
    )
    parser.add_argument("--to-offset", help="目标 UTC 偏移，如 -07:00")
    parser.add_argument(
        "--set-offset", help="写入 EXIF 时区字段（仅图片）"
    )
    parser.add_argument(
        "--ext",
        action="append",
        default=[],
        help="追加处理的扩展名（可重复）",
    )
    parser.add_argument(
        "--no-file-times",
        action="store_true",
        help="不修改文件系统时间",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="并行复制 / ExifTool 任务数。默认 4",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只打印将执行的 ExifTool 命令，不复制也不修改",
    )


def run(args: argparse.Namespace) -> int:
    try:
        shift_seconds = timeshift.compute_shift_seconds(
            args.shift, args.from_offset, args.to_offset
        )
        if args.set_offset:
            timeshift.parse_offset(args.set_offset)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return USAGE

    if shift_seconds == 0 and not args.set_offset:
        print(
            "ERROR: nothing to do: the shift is zero and --set-offset is not set.",
            file=sys.stderr,
        )
        return USAGE

    if args.workers <= 0:
        print("ERROR: --workers must be > 0", file=sys.stderr)
        return USAGE

    in_root = pathlib.Path(args.input).resolve()
    if not in_root.is_dir():
        print(f"ERROR: input directory not found: {in_root}", file=sys.stderr)
        return USAGE

    in_place = args.in_place
    out_root = in_root if in_place else pathlib.Path(args.output).resolve()

    try:
        version = require_exiftool(optional=args.dry_run)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return USAGE

    if version is None:
        print("WARNING: ExifTool not found; --dry-run only prints commands.")

    exts = DEFAULT_EXTS | fsutil.normalize_exts(args.ext)
    files = list(
        fsutil.iter_files(
            in_root, exts, exclude_dirs=[] if in_place else [out_root]
        )
    )
    if not files:
        print(f"No matching files found in {in_root}")
        return NO_INPUT

    plan = FixPlan(
        files=files,
        in_root=in_root,
        out_root=out_root,
        in_place=in_place,
        shift_seconds=shift_seconds,
        set_offset=args.set_offset,
        no_file_times=args.no_file_times,
        workers=args.workers,
        exiftool_version=version,
    )
    print(plan.describe())
    print()

    reporter = ConsoleReporter()
    plan.execute(dry_run=args.dry_run, reporter=reporter)
    if args.dry_run:
        print("（dry-run，未实际执行）")
        return OK
    if plan.interrupted:
        print("\nInterrupted.", file=sys.stderr)
        return INTERRUPTED

    for message in plan.copy_errors:
        print(f"COPY ERROR: {message}", file=sys.stderr)
    for message in plan.errors:
        print(f"ERROR: {message}", file=sys.stderr)
    for message in plan.file_time_errors:
        print(f"WARNING (file times): {message}", file=sys.stderr)

    print()
    print("Done")
    print("Updated :", plan.updated)
    print("Skipped :", plan.skipped, "(no applicable tags for this file type)")
    print("Failed  :", len(plan.errors) + len(plan.copy_errors))
    return PARTIAL_FAIL if (plan.errors or plan.copy_errors) else OK
