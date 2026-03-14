#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Universal Photo Time Fix Tool
==============================

Purpose
-------
Fix incorrect timestamps in Sony RAW / HEIF photos while preserving pixel data.

Typical scenarios:
1. DST mistake (camera timezone correct but DST disabled)
2. Wrong timezone setting on camera
3. Manual correction of capture time

This script:
- Recursively scans input directory
- Copies files to output directory
- Uses ExifTool to shift metadata timestamps
- Updates EXIF timezone metadata
- Updates filesystem timestamps
- Synchronizes thumbnail metadata

Supported formats
-----------------
ARW, HIF, HEIF, HEIC (can extend)

Metadata fields updated
-----------------------
EXIF:
    DateTimeOriginal
    CreateDate
    ModifyDate
    OffsetTime
    OffsetTimeOriginal
    OffsetTimeDigitized

XMP:
    CreateDate
    ModifyDate
    DateCreated

Filesystem:
    FileModifyDate
    FileCreateDate

Preview:
    IFD1:ModifyDate

Important
---------
Pixel data is NOT modified.
ExifTool only rewrites metadata segments.

Requirements
------------
Install exiftool:

    brew install exiftool
"""

import argparse
import shutil
import subprocess
from pathlib import Path
from typing import Iterable, Tuple, List


DEFAULT_EXTS = {
    ".arw",
    ".hif",
    ".heif",
    ".heic",
    ".jpg",
    ".jpeg",
    ".mp4"
}


# ------------------------------------------------------------
# Utilities
# ------------------------------------------------------------

def require_exiftool() -> None:
    """
    Ensure exiftool exists in PATH.
    """
    try:
        subprocess.run(["exiftool", "-ver"], check=True,
                       stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE)
    except Exception:
        raise SystemExit("ExifTool not found. Install with: brew install exiftool")


def iter_files(root: Path, exts: Iterable[str]):
    """
    Recursively yield files with matching extensions.
    """
    exts = {e.lower() for e in exts}
    for p in root.rglob("*"):
        if p.is_file() and p.suffix.lower() in exts:
            yield p


def ensure_parent(p: Path):
    p.parent.mkdir(parents=True, exist_ok=True)


def copy_preserve_structure(src: Path, src_root: Path, dst_root: Path) -> Path:
    """
    Copy file while preserving folder structure.
    """
    rel = src.relative_to(src_root)
    dst = dst_root / rel
    ensure_parent(dst)
    shutil.copy2(src, dst)
    return dst


# ------------------------------------------------------------
# Time calculation
# ------------------------------------------------------------

def parse_offset(offset: str) -> int:
    """
    Convert timezone offset to minutes.

    Example:
        -08:00 -> -480
        +09:30 -> 570
    """
    sign = -1 if offset.startswith("-") else 1
    hh, mm = offset[1:].split(":")
    return sign * (int(hh) * 60 + int(mm))


def compute_shift_minutes(shift: str,
                          from_offset: str,
                          to_offset: str) -> int:
    """
    Determine total shift in minutes.

    Priority:
        1. explicit shift
        2. offset conversion
    """

    if shift:
        sign = -1 if shift.startswith("-") else 1
        hh, mm, ss = shift[1:].split(":")
        return sign * (int(hh) * 60 + int(mm))

    if from_offset and to_offset:
        return parse_offset(to_offset) - parse_offset(from_offset)

    raise SystemExit("Must provide either --shift or (--from-offset and --to-offset)")


def shift_to_exiftool(minutes: int) -> str:
    """
    Convert minutes shift to ExifTool syntax.

    Example:
        +60 -> +=1:00:00
    """
    sign = "+=" if minutes >= 0 else "-="
    minutes = abs(minutes)

    h = minutes // 60
    m = minutes % 60

    return f"{sign}{h}:{m:02d}:00"


# ------------------------------------------------------------
# ExifTool execution
# ------------------------------------------------------------

def run_exiftool(file: Path,
                 shift_expr: str,
                 set_offset: str,
                 dry_run: bool) -> Tuple[int, str, str]:
    """
    Execute exiftool metadata update.
    """

    cmd: List[str] = [
        "exiftool",
        "-overwrite_original",
        "-m",
        "-api", "QuickTimeUTC=1",

        # EXIF times
        f"-AllDates{shift_expr}",

        # XMP times
        f"-XMP:CreateDate{shift_expr}",
        f"-XMP:ModifyDate{shift_expr}",
        f"-XMP:DateCreated{shift_expr}",

        # filesystem timestamps
        f"-FileModifyDate{shift_expr}",
        f"-FileCreateDate{shift_expr}",

        # preview / thumbnail metadata
        f"-IFD1:ModifyDate{shift_expr}",
    ]

    if set_offset:
        cmd += [
            f"-OffsetTime={set_offset}",
            f"-OffsetTimeOriginal={set_offset}",
            f"-OffsetTimeDigitized={set_offset}",
        ]

    cmd.append(str(file))

    if dry_run:
        return 0, "DRY RUN: " + " ".join(cmd), ""

    proc = subprocess.run(cmd,
                          stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE,
                          text=True)

    return proc.returncode, proc.stdout, proc.stderr


# ------------------------------------------------------------
# CLI
# ------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(
        description="Fix photo timestamp errors (DST / timezone / manual shift)"
    )

    parser.add_argument("-i", "--input", required=True,
                        help="Input directory")

    parser.add_argument("-o", "--output", default="output",
                        help="Output directory")

    parser.add_argument("--shift",
                        help="Manual time shift (format +HH:MM:SS)")

    parser.add_argument("--from-offset",
                        help="Original timezone offset (e.g. -08:00)")

    parser.add_argument("--to-offset",
                        help="Target timezone offset (e.g. -07:00)")

    parser.add_argument("--set-offset",
                        help="Write EXIF timezone offset")

    parser.add_argument("--ext",
                        action="append",
                        default=[],
                        help="Extra file extension")

    parser.add_argument("--dry-run",
                        action="store_true",
                        help="Print commands only")

    return parser.parse_args()


# ------------------------------------------------------------
# Main
# ------------------------------------------------------------

def main():

    args = parse_args()

    require_exiftool()

    input_dir = Path(args.input).resolve()
    output_dir = Path(args.output).resolve()

    exts = DEFAULT_EXTS | {("." + e.strip(".")) for e in args.ext}

    files = list(iter_files(input_dir, exts))

    if not files:
        print("No matching files found")
        return

    shift_minutes = compute_shift_minutes(args.shift,
                                          args.from_offset,
                                          args.to_offset)

    shift_expr = shift_to_exiftool(shift_minutes)

    print("Parameters")
    print("----------")
    print("Input:", input_dir)
    print("Output:", output_dir)
    print("Files:", len(files))
    print("Shift minutes:", shift_minutes)
    print("ExifTool shift:", shift_expr)
    print("Set offset:", args.set_offset)
    print()

    ok = 0
    failed = 0

    for src in files:

        dst = copy_preserve_structure(src, input_dir, output_dir)

        rc, out, err = run_exiftool(dst,
                                    shift_expr,
                                    args.set_offset,
                                    args.dry_run)

        if rc == 0:
            ok += 1
        else:
            failed += 1
            print("Failed:", dst)
            print(err)

    print()
    print("Done")
    print("Success:", ok)
    print("Failed:", failed)


if __name__ == "__main__":
    main()