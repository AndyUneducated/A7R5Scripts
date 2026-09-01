"""Batch-shrink photos to HEIF or JPEG.

Behaviour worth knowing:
- The input tree is mirrored into the output directory.
- EXIF Orientation is baked into the pixels, and EXIF/ICC are carried over
  (RAW metadata is recovered from the embedded preview).
- Transparency is composited onto a background colour.
- --max-size searches for the best encoding entirely in memory.
"""

from __future__ import annotations

import os
import pathlib
from collections.abc import Iterable, Sequence
from concurrent.futures import ProcessPoolExecutor, as_completed
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass, field

from mediakit.core import fsutil, progress
from mediakit.core.pipeline import NullReporter, Reporter
from . import imaging

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


def build_pairs(
    inputs: Iterable[pathlib.Path],
    in_root: pathlib.Path,
    out_root: pathlib.Path,
    out_ext: str,
    naming: str,
) -> tuple[list[tuple[pathlib.Path, pathlib.Path]], list[str]]:
    """Pair every input with a unique output path inside the mirrored tree."""
    pairs: list[tuple[pathlib.Path, pathlib.Path]] = []
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
        pairs.append((src, dst))

    return pairs, warnings


# Kept as an alias so older tests / call sites can still say build_plan.
build_plan = build_pairs


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
    pairs: Sequence[tuple[pathlib.Path, pathlib.Path]],
    opts: Options,
    workers: int,
) -> tuple[list[Result], bool]:
    """Execute the pairs. Returns the results and whether it was interrupted."""
    results: list[Result] = []

    if workers == 1:
        try:
            for src, dst in progress.iterate(pairs, len(pairs), "Processing"):
                results.append(process_one(str(src), str(dst), opts))
        except KeyboardInterrupt:
            return results, True
        return results, False

    try:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = [
                pool.submit(process_one, str(src), str(dst), opts) for src, dst in pairs
            ]
            for future in progress.iterate(
                as_completed(futures), len(futures), "Processing"
            ):
                results.append(future.result())
    except KeyboardInterrupt:
        return results, True
    except BrokenProcessPool:
        return results, True

    return results, False


@dataclass
class ShrinkPlan:
    """A describable, executable batch of photo conversions."""

    pairs: list[tuple[pathlib.Path, pathlib.Path]]
    options: Options
    workers: int
    warnings: list[str] = field(default_factory=list)
    results: list[Result] = field(default_factory=list)
    interrupted: bool = False
    pool_broken: bool = False

    def describe(self) -> str:
        opts = self.options
        edge = "unlimited" if opts.max_edge is None else f"{opts.max_edge}px"
        size = (
            fsutil.human_bytes(opts.max_bytes)
            if opts.max_bytes is not None
            else "unset"
        )
        return "\n".join(
            [
                "  操作      : 批量压缩照片",
                f"  文件数    : {len(self.pairs)}",
                f"  输出格式  : {opts.out_format}",
                f"  最长边    : {edge}",
                f"  质量      : {opts.quality}",
                f"  体积上限  : {size}",
                f"  并行      : {self.workers}",
            ]
        )

    def execute(
        self, *, dry_run: bool = False, reporter: Reporter | None = None
    ) -> list[pathlib.Path]:
        reporter = reporter or NullReporter()
        if dry_run:
            total = len(self.pairs)
            for index, (src, dst) in enumerate(self.pairs, start=1):
                reporter.note(f"[{index}/{total}] {src} -> {dst}")
            return []

        before = len(self.results)
        results, interrupted = _run(self.pairs, self.options, self.workers)
        self.results = results
        self.interrupted = interrupted
        if interrupted and len(results) < len(self.pairs) and self.workers > 1:
            # Distinguish a dead worker (OOM) from Ctrl-C when we can.
            self.pool_broken = True
        written = [pathlib.Path(r.dst) for r in results if r.status == STATUS_OK]
        reporter.note(f"converted {len(written)}/{len(self.pairs)}")
        return written


def summarize(plan: ShrinkPlan) -> str:
    """Human-readable totals after execute()."""
    results = plan.results
    done = [r for r in results if r.status == STATUS_OK]
    skipped = [r for r in results if r.status == STATUS_SKIPPED]
    failed = [r for r in results if r.status == STATUS_ERROR]
    lines: list[str] = []
    if failed:
        lines.append("--- Errors ---")
        for result in failed[:50]:
            lines.append(f"ERROR: {result.error}")
        if len(failed) > 50:
            lines.append(f"... and {len(failed) - 50} more")
        lines.append("")
    if plan.interrupted:
        lines.append(f"Interrupted after {len(results)}/{len(plan.pairs)} files")
    if plan.pool_broken:
        lines.append(
            "ERROR: a worker process died, most likely out of memory.\n"
            "A single high-resolution RAW needs roughly 200 MB per worker; "
            "retry with fewer --workers."
        )
    before = sum(r.before for r in done)
    after = sum(r.after for r in done)
    lines += [
        f"Converted    : {len(done)}",
        f"Skipped      : {len(skipped)} (already existed; use --overwrite)",
        f"Failed       : {len(failed)}",
    ]
    if done:
        lines.append(f"Total before : {fsutil.human_bytes(before)}")
        lines.append(f"Total after  : {fsutil.human_bytes(after)}")
        if before:
            lines.append(f"Reduction    : {100 - after / before * 100:.1f}%")
    lines.append(f"Output format: {plan.options.out_format}")
    return "\n".join(lines)
