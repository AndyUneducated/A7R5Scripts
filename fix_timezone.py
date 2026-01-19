#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Fix timezone mistake for SONY A7R V files (ARW / HIF / HEIF).

Use ExifTool to shift *all relevant datetime tags* by a fixed offset, and
write corrected copies into an output folder.

Example:
  - File timestamps were recorded as if UTC-8, but should be UTC+8
  - Offset = +16 hours

Supported:
  - ARW (Sony RAW)
  - HIF / HEIF
  - plus common photo/video formats if present

Notes (important):
  - This modifies metadata fields such as DateTimeOriginal/CreateDate/ModifyDate,
    and also attempts filesystem times (FileModifyDate/FileCreateDate) in the
    copied output files.
  - Some OS/filesystems may restrict setting FileCreateDate; ExifTool will do
    best-effort.
"""

import argparse
import os
import shutil
import subprocess
from pathlib import Path
from typing import Iterable, List, Tuple


DEFAULT_EXTS = {".arw", ".hif", ".heif", ".heic"}  # include common HEIF/HEIC


def require_exiftool() -> None:
    try:
        subprocess.run(["exiftool", "-ver"], check=True, capture_output=True, text=True)
    except Exception as e:
        raise SystemExit(
            "未检测到 exiftool。请先安装并确保命令行可运行 `exiftool -ver`。\n"
            f"错误: {e}"
        )


def iter_files(input_dir: Path, exts: Iterable[str]) -> Iterable[Path]:
    exts_lc = {e.lower() for e in exts}
    for p in input_dir.rglob("*"):
        if p.is_file() and p.suffix.lower() in exts_lc:
            yield p


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def copy_preserve_structure(src: Path, input_dir: Path, output_dir: Path) -> Path:
    rel = src.relative_to(input_dir)
    dst = output_dir / rel
    ensure_parent(dst)
    shutil.copy2(src, dst)  # copy2 tries to keep filesystem mtime; we will rewrite later
    return dst


def exiftool_shift(
    file_path: Path,
    hours: int,
    minutes: int,
    seconds: int,
    dry_run: bool = False,
) -> Tuple[int, str, str]:
    """
    Shift datetime tags by +/-(hours:minutes:seconds) using ExifTool.

    We update:
      - AllDates (maps to DateTimeOriginal/CreateDate/ModifyDate when present)
      - XMP:CreateDate / XMP:ModifyDate (often exists in sidecars/embedded XMP)
      - QuickTime/HEIF common create/modify if applicable via AllDates
      - FileModifyDate / FileCreateDate (filesystem timestamps) on output file

    Return: (returncode, stdout, stderr)
    """
    sign = "+" if hours >= 0 else "-"
    h = abs(hours)

    # ExifTool time shift format: +=HH:MM:SS or -=HH:MM:SS
    shift = f"{sign}={h}:{abs(minutes)}:{abs(seconds)}"

    # -overwrite_original: do not create _original backups in output folder
    # -m: ignore minor warnings
    # -api QuickTimeUTC=1: helps with some QuickTime/HEIF time handling (best-effort)
    cmd: List[str] = [
        "exiftool",
        "-m",
        "-overwrite_original",
        "-api",
        "QuickTimeUTC=1",
        f"-AllDates{shift}",
        f"-XMP:CreateDate{shift}",
        f"-XMP:ModifyDate{shift}",
        f"-FileModifyDate{shift}",
        f"-FileCreateDate{shift}",
        str(file_path),
    ]

    if dry_run:
        # -echo4 prints what would be done; keep it simple: just show the command
        return (0, "DRY_RUN: " + " ".join(cmd), "")

    proc = subprocess.run(cmd, capture_output=True, text=True)
    return (proc.returncode, proc.stdout, proc.stderr)


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="纠正 SONY A7R V（ARW/HIF/HEIF）因时区设置错误导致的时间 metadata 偏移。"
    )
    ap.add_argument(
        "-i", "--input",
        required=True,
        help="输入文件夹（递归扫描）",
    )
    ap.add_argument(
        "-o", "--output",
        default="output",
        help="输出文件夹（默认: ./output）",
    )
    ap.add_argument(
        "--from-utc",
        default="-8",
        help="错误设置的 UTC offset（默认: -8）",
    )
    ap.add_argument(
        "--to-utc",
        default="+8",
        help="正确的 UTC offset（默认: +8）",
    )
    ap.add_argument(
        "--ext",
        action="append",
        default=[],
        help="额外处理的扩展名（可多次指定），如: --ext .jpg --ext .mp4",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="只复制文件并打印将要执行的 exiftool 命令，不实际写入 metadata",
    )
    return ap.parse_args()


def parse_utc_offset(s: str) -> int:
    """
    Parse strings like '+8', '-8', '8' into integer hours.
    """
    s = s.strip()
    if s.startswith("UTC"):
        s = s[3:].strip()
    if s.startswith("+"):
        return int(s[1:])
    if s.startswith("-"):
        return -int(s[1:])
    return int(s)


def main() -> None:
    args = parse_args()
    require_exiftool()

    input_dir = Path(args.input).expanduser().resolve()
    output_dir = Path(args.output).expanduser().resolve()

    if not input_dir.exists() or not input_dir.is_dir():
        raise SystemExit(f"输入路径不是文件夹或不存在: {input_dir}")

    from_utc = parse_utc_offset(args.from_utc)
    to_utc = parse_utc_offset(args.to_utc)

    delta_hours = to_utc - from_utc  # e.g. +8 - (-8) = +16

    exts = set(DEFAULT_EXTS)
    for e in args.ext:
        if not e.startswith("."):
            e = "." + e
        exts.add(e.lower())

    files = list(iter_files(input_dir, exts))
    if not files:
        print(f"未找到匹配的文件。输入目录: {input_dir}，扩展名: {sorted(exts)}")
        return

    print("参数汇总")
    print(f"- 输入目录: {input_dir}")
    print(f"- 输出目录: {output_dir}")
    print(f"- 纠正: UTC{from_utc:+d} -> UTC{to_utc:+d}")
    print(f"- 时间平移: {delta_hours:+d} 小时")
    print(f"- 扩展名: {sorted(exts)}")
    print(f"- 文件数量: {len(files)}")
    print()

    ok = 0
    failed = 0

    for src in files:
        dst = copy_preserve_structure(src, input_dir, output_dir)
        rc, out, err = exiftool_shift(
            dst,
            hours=delta_hours,
            minutes=0,
            seconds=0,
            dry_run=args.dry_run,
        )

        if rc == 0:
            ok += 1
        else:
            failed += 1

        # 简洁输出：失败打印 stderr；dry-run 打印命令
        if args.dry_run:
            print(out)
        elif rc != 0:
            print(f"[失败] {dst}")
            if out.strip():
                print(out.strip())
            if err.strip():
                print(err.strip())
        else:
            # 正常情况下 exiftool 会打印 "1 image files updated"
            pass

    print()
    print("完成")
    print(f"- 成功: {ok}")
    print(f"- 失败: {failed}")
    print(f"- 输出目录: {output_dir}")


if __name__ == "__main__":
    main()
