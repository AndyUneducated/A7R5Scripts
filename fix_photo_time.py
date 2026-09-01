#!/usr/bin/env python3
"""
Fix photo and video timestamps without touching pixel data.

Typical scenarios:
1. DST mistake (timezone correct on the camera, DST disabled)
2. Wrong timezone set on the camera
3. Manual correction of capture time

The script shifts metadata dates with ExifTool, writes the EXIF UTC offset,
and finally derives the filesystem dates from the corrected capture time.
Pixel data is never re-encoded.

Requires ExifTool:

    brew install exiftool
"""

from __future__ import annotations

import argparse
import pathlib
import shlex
import shutil
import subprocess
import sys
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

from a7r5 import fsutil, progress, timeshift

IMAGE_EXTS = {
    ".arw",
    ".dng",
    ".heic",
    ".heif",
    ".hif",
    ".jpeg",
    ".jpg",
    ".tif",
    ".tiff",
}
VIDEO_EXTS = {".m4v", ".mov", ".mp4"}
DEFAULT_EXTS = IMAGE_EXTS | VIDEO_EXTS

# Keeps each exiftool argv comfortably below the platform argument limit.
BATCH_SIZE = 150

IMAGE_KIND = "image"
VIDEO_KIND = "video"


@dataclass
class Batch:
    kind: str
    files: list[pathlib.Path]


def require_exiftool(optional: bool = False) -> str | None:
    """Return the ExifTool version, or fail with install instructions."""
    try:
        proc = subprocess.run(
            ["exiftool", "-ver"], capture_output=True, text=True, check=True
        )
        return proc.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        if optional:
            return None
        raise SystemExit("ExifTool not found. Install with: brew install exiftool")


def kind_of(path: pathlib.Path) -> str:
    return VIDEO_KIND if path.suffix.lower() in VIDEO_EXTS else IMAGE_KIND


def shift_args(kind: str, expr: str, set_offset: str | None) -> list[str]:
    """Tags to shift, per file kind."""
    args: list[str] = []
    if expr:
        args.append(f"-AllDates{expr}")
        args += [f"-XMP:CreateDate{expr}", f"-XMP:ModifyDate{expr}"]
        if kind == IMAGE_KIND:
            args += [f"-XMP:DateCreated{expr}", f"-IFD1:ModifyDate{expr}"]
        else:
            # AllDates only covers QuickTime:CreateDate/ModifyDate; the track
            # and media dates are separate tags and Finder reads them too.
            args += [
                f"-QuickTime:TrackCreateDate{expr}",
                f"-QuickTime:TrackModifyDate{expr}",
                f"-QuickTime:MediaCreateDate{expr}",
                f"-QuickTime:MediaModifyDate{expr}",
            ]

    if set_offset and kind == IMAGE_KIND:
        args += [
            f"-OffsetTime={set_offset}",
            f"-OffsetTimeOriginal={set_offset}",
            f"-OffsetTimeDigitized={set_offset}",
        ]
    return args


def file_time_args(kind: str) -> list[str]:
    """
    Derive the filesystem dates from the corrected capture time.

    Shifting FileCreateDate would shift the time the *copy* was made, so the
    value is copied from the metadata instead.
    """
    source = "DateTimeOriginal" if kind == IMAGE_KIND else "QuickTime:CreateDate"
    return [f"-FileModifyDate<{source}", f"-FileCreateDate<{source}"]


def build_command(args: Sequence[str], files: Sequence[pathlib.Path]) -> list[str]:
    return [
        "exiftool",
        "-overwrite_original",
        "-m",
        "-api",
        "QuickTimeUTC=1",
        *args,
        *(str(f) for f in files),
    ]


def run_exiftool(
    args: Sequence[str], files: Sequence[pathlib.Path]
) -> tuple[int, str, str]:
    proc = subprocess.run(
        build_command(args, files), capture_output=True, text=True
    )
    return proc.returncode, proc.stdout, proc.stderr


def chunk(files: Sequence[pathlib.Path], size: int) -> list[list[pathlib.Path]]:
    return [list(files[i : i + size]) for i in range(0, len(files), size)]


def copy_all(
    files: Sequence[pathlib.Path],
    in_root: pathlib.Path,
    out_root: pathlib.Path,
    workers: int,
) -> tuple[list[pathlib.Path], list[str]]:
    """Copy the tree into `out_root`, preserving structure. Returns copies."""
    copied: list[pathlib.Path] = []
    errors: list[str] = []

    def copy_one(src: pathlib.Path) -> pathlib.Path:
        dst = fsutil.mirror_path(src, in_root, out_root)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        return dst

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(copy_one, src): src for src in files}
        for future in progress.iterate(
            as_completed(futures), len(futures), "Copying"
        ):
            src = futures[future]
            try:
                copied.append(future.result())
            except Exception as exc:
                errors.append(f"{src} -> {exc!r}")

    return copied, errors


def apply_batches(
    batches: Sequence[Batch],
    args_by_kind: dict[str, list[str]],
    workers: int,
    desc: str,
) -> tuple[int, int, list[str]]:
    """
    Run ExifTool over the batches in parallel.

    A failing batch is retried file by file so the report names the actual
    offenders instead of the whole chunk. Returns (updated, skipped, errors).
    """
    errors: list[str] = []
    updated = 0
    skipped = 0

    def run_batch(batch: Batch) -> tuple[int, int, list[str]]:
        args = args_by_kind[batch.kind]
        if not args:
            return 0, len(batch.files), []
        code, _, _ = run_exiftool(args, batch.files)
        if code == 0:
            return len(batch.files), 0, []

        ok = 0
        failures: list[str] = []
        for path in batch.files:
            code, _, err = run_exiftool(args, [path])
            if code == 0:
                ok += 1
            else:
                failures.append(f"{path} -> {err.strip() or f'exit code {code}'}")
        return ok, 0, failures

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(run_batch, batch) for batch in batches]
        for future in progress.iterate(as_completed(futures), len(futures), desc):
            ok, no_op, failures = future.result()
            updated += ok
            skipped += no_op
            errors.extend(failures)

    return updated, skipped, errors


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fix photo timestamp errors (DST / timezone / manual shift)"
    )
    parser.add_argument("-i", "--input", required=True, help="Input directory")
    parser.add_argument(
        "-o",
        "--output",
        default="output",
        help="Output directory (input tree is mirrored). Default: output",
    )
    parser.add_argument(
        "--in-place",
        action="store_true",
        help="Modify the input files directly instead of copying them first",
    )
    parser.add_argument("--shift", help="Manual time shift, format ±HH:MM:SS")
    parser.add_argument(
        "--from-offset", help="Original UTC offset the camera recorded, e.g. -08:00"
    )
    parser.add_argument("--to-offset", help="Target UTC offset, e.g. -07:00")
    parser.add_argument(
        "--set-offset", help="UTC offset to write into the EXIF offset tags"
    )
    parser.add_argument(
        "--ext",
        action="append",
        default=[],
        help="Extra file extension to include (repeatable)",
    )
    parser.add_argument(
        "--no-file-times",
        action="store_true",
        help="Do not touch filesystem dates",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Parallel copy / ExifTool jobs. Default: 4",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the ExifTool commands without copying or modifying anything",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)

    try:
        shift_seconds = timeshift.compute_shift_seconds(
            args.shift, args.from_offset, args.to_offset
        )
        if args.set_offset:
            timeshift.parse_offset(args.set_offset)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if shift_seconds == 0 and not args.set_offset:
        print(
            "ERROR: nothing to do: the shift is zero and --set-offset is not set.",
            file=sys.stderr,
        )
        return 2

    if args.workers <= 0:
        print("ERROR: --workers must be > 0", file=sys.stderr)
        return 2

    in_root = pathlib.Path(args.input).resolve()
    if not in_root.is_dir():
        print(f"ERROR: input directory not found: {in_root}", file=sys.stderr)
        return 2

    in_place = args.in_place
    out_root = in_root if in_place else pathlib.Path(args.output).resolve()

    version = require_exiftool(optional=args.dry_run)
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
        return 1

    expr = timeshift.exiftool_shift_expr(shift_seconds) if shift_seconds else ""

    print("Parameters")
    print("----------")
    print("ExifTool     :", version or "not found")
    print("Input        :", in_root)
    print("Output       :", "in place" if in_place else out_root)
    print("Files        :", len(files))
    print("Shift        :", timeshift.format_shift(shift_seconds))
    print("ExifTool arg :", expr or "(no date shift)")
    print("Set offset   :", args.set_offset or "(unchanged)")
    print("File times   :", "from capture time" if not args.no_file_times else "kept")
    print()

    kinds = sorted({kind_of(f) for f in files})
    if args.dry_run:
        print("DRY RUN: commands that would run (file list truncated)")
        for kind in kinds:
            of_kind = [f for f in files if kind_of(f) == kind]
            print(f"\n[{kind}] {len(of_kind)} files")
            print(
                shlex.join(
                    build_command(
                        shift_args(kind, expr, args.set_offset), of_kind[:2]
                    )
                )
            )
            if not args.no_file_times:
                print(shlex.join(build_command(file_time_args(kind), of_kind[:2])))
        return 0

    try:
        if in_place:
            targets = files
            copy_errors: list[str] = []
        else:
            targets, copy_errors = copy_all(files, in_root, out_root, args.workers)

        batches = [
            Batch(kind, group)
            for kind in kinds
            for group in chunk([t for t in targets if kind_of(t) == kind], BATCH_SIZE)
        ]

        updated, skipped, errors = apply_batches(
            batches,
            {kind: shift_args(kind, expr, args.set_offset) for kind in kinds},
            args.workers,
            "Shifting",
        )

        file_time_errors: list[str] = []
        if not args.no_file_times:
            *_, file_time_errors = apply_batches(
                batches,
                {kind: file_time_args(kind) for kind in kinds},
                args.workers,
                "File times",
            )
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130

    for message in copy_errors:
        print(f"COPY ERROR: {message}", file=sys.stderr)
    for message in errors:
        print(f"ERROR: {message}", file=sys.stderr)
    for message in file_time_errors:
        print(f"WARNING (file times): {message}", file=sys.stderr)

    print()
    print("Done")
    print("Updated :", updated)
    print("Skipped :", skipped, "(no applicable tags for this file type)")
    print("Failed  :", len(errors) + len(copy_errors))

    return 10 if (errors or copy_errors) else 0


if __name__ == "__main__":
    sys.exit(main())
