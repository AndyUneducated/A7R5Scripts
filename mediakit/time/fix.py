"""Shift photo and video capture times without touching pixel data.

Typical scenarios:
1. DST mistake (timezone correct on the camera, DST disabled)
2. Wrong timezone set on the camera
3. Manual correction of capture time

ExifTool rewrites time tags; pixels are never re-encoded.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

from mediakit.core import fsutil, progress
from mediakit.core.pipeline import NullReporter, Reporter
from . import timeshift

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
        raise RuntimeError("ExifTool not found. Install with: brew install exiftool")


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


@dataclass
class FixPlan:
    """A describable, executable capture-time repair."""

    files: list[pathlib.Path]
    in_root: pathlib.Path
    out_root: pathlib.Path
    in_place: bool
    shift_seconds: int
    set_offset: str | None
    no_file_times: bool
    workers: int
    exiftool_version: str | None
    warnings: list[str] = field(default_factory=list)
    copy_errors: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    file_time_errors: list[str] = field(default_factory=list)
    updated: int = 0
    skipped: int = 0
    interrupted: bool = False

    def describe(self) -> str:
        expr = (
            timeshift.exiftool_shift_expr(self.shift_seconds)
            if self.shift_seconds
            else "(no date shift)"
        )
        return "\n".join(
            [
                "  操作      : 修复拍摄时间 metadata（不重新编码）",
                f"  ExifTool  : {self.exiftool_version or 'not found'}",
                f"  输入      : {self.in_root}",
                f"  输出      : {'in place' if self.in_place else self.out_root}",
                f"  文件数    : {len(self.files)}",
                f"  偏移      : {timeshift.format_shift(self.shift_seconds)}",
                f"  ExifTool  : {expr}",
                f"  写入时区  : {self.set_offset or '(unchanged)'}",
                f"  文件时间  : {'from capture time' if not self.no_file_times else 'kept'}",
            ]
        )

    def execute(
        self, *, dry_run: bool = False, reporter: Reporter | None = None
    ) -> list[pathlib.Path]:
        reporter = reporter or NullReporter()
        expr = (
            timeshift.exiftool_shift_expr(self.shift_seconds)
            if self.shift_seconds
            else ""
        )
        kinds = sorted({kind_of(f) for f in self.files})

        if dry_run:
            for kind in kinds:
                of_kind = [f for f in self.files if kind_of(f) == kind]
                reporter.note(f"[{kind}] {len(of_kind)} files")
                reporter.command(
                    build_command(shift_args(kind, expr, self.set_offset), of_kind[:2])
                )
                if not self.no_file_times:
                    reporter.command(build_command(file_time_args(kind), of_kind[:2]))
            return []

        try:
            if self.in_place:
                targets = self.files
                self.copy_errors = []
            else:
                targets, self.copy_errors = copy_all(
                    self.files, self.in_root, self.out_root, self.workers
                )

            batches = [
                Batch(kind, group)
                for kind in kinds
                for group in chunk(
                    [t for t in targets if kind_of(t) == kind], BATCH_SIZE
                )
            ]

            self.updated, self.skipped, self.errors = apply_batches(
                batches,
                {kind: shift_args(kind, expr, self.set_offset) for kind in kinds},
                self.workers,
                "Shifting",
            )

            if not self.no_file_times:
                *_, self.file_time_errors = apply_batches(
                    batches,
                    {kind: file_time_args(kind) for kind in kinds},
                    self.workers,
                    "File times",
                )
        except KeyboardInterrupt:
            self.interrupted = True
            return []

        return list(targets)
