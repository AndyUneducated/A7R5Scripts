"""Filesystem scanning helpers shared by the scripts."""

from __future__ import annotations

import os
import pathlib
from collections.abc import Iterable, Iterator


def normalize_exts(exts: Iterable[str]) -> set[str]:
    """Normalize extensions to lowercase, dot-prefixed form."""
    out: set[str] = set()
    for ext in exts:
        cleaned = ext.strip().lower()
        if not cleaned:
            continue
        out.add(cleaned if cleaned.startswith(".") else f".{cleaned}")
    return out


def is_ignorable(name: str) -> bool:
    """
    True for names that should never be treated as photo input.

    AppleDouble sidecars written to exFAT cards ("._DSC00001.ARW") carry the
    same extension as the real file, so extension matching alone picks them up.
    """
    return name.startswith(".") or name == "__MACOSX"


def iter_files(
    root: str | os.PathLike[str],
    exts: Iterable[str],
    exclude_dirs: Iterable[str | os.PathLike[str]] = (),
) -> Iterator[pathlib.Path]:
    """
    Recursively yield files under `root` whose extension is in `exts`.

    Directories in `exclude_dirs` are pruned together with their subtrees, which
    keeps an output directory nested inside the input directory from being
    scanned as input.
    """
    root_p = pathlib.Path(root)
    wanted = normalize_exts(exts)
    excluded = {pathlib.Path(d).resolve() for d in exclude_dirs}

    for current, dirs, files in os.walk(root_p):
        current_p = pathlib.Path(current)
        dirs[:] = sorted(
            d
            for d in dirs
            if not is_ignorable(d) and (current_p / d).resolve() not in excluded
        )
        for name in sorted(files):
            if is_ignorable(name):
                continue
            path = current_p / name
            if path.suffix.lower() in wanted:
                yield path


def mirror_path(
    src: str | os.PathLike[str],
    src_root: str | os.PathLike[str],
    dst_root: str | os.PathLike[str],
    name: str | None = None,
) -> pathlib.Path:
    """Map `src` into `dst_root`, preserving its path relative to `src_root`."""
    src_p = pathlib.Path(src)
    rel = pathlib.Path(os.path.relpath(src_p, pathlib.Path(src_root)))
    return pathlib.Path(dst_root) / rel.parent / (name or src_p.name)


def human_bytes(size: float) -> str:
    """Format a byte count using binary units."""
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(value) < 1024 or unit == "TiB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TiB"


def parse_size(text: str) -> int:
    """
    Parse a byte size such as `3mb`, `3000kb`, `3.5MiB` or a raw byte count.

    Units are binary: 1mb == 1 MiB == 1048576 bytes.
    """
    raw = text.strip().lower().replace(" ", "").replace("i", "")
    if not raw:
        raise ValueError("Empty size value. Examples: 3mb, 3000kb, 3145728")

    multipliers = (
        ("gb", 1024**3),
        ("g", 1024**3),
        ("mb", 1024**2),
        ("m", 1024**2),
        ("kb", 1024),
        ("k", 1024),
        ("b", 1),
    )
    number = raw
    multiplier = 1
    for suffix, factor in multipliers:
        if raw.endswith(suffix) and raw[: -len(suffix)]:
            number = raw[: -len(suffix)]
            multiplier = factor
            break

    try:
        value = float(number)
    except ValueError:
        raise ValueError(
            f"Invalid size {text!r}. Examples: 3mb, 3000kb, 3145728"
        ) from None
    if value <= 0:
        raise ValueError(f"Size must be > 0, got {text!r}")
    return int(value * multiplier)
