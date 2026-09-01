"""Progress reporting with a plain fallback when tqdm is unavailable."""

from __future__ import annotations

import sys
from collections.abc import Iterable, Iterator


def iterate(items: Iterable, total: int, desc: str) -> Iterator:
    """Wrap `items` in a progress bar, or a terse counter without tqdm."""
    try:
        from tqdm import tqdm
    except ImportError:
        return _counter(items, total, desc)
    return iter(tqdm(items, total=total, desc=desc))


def _counter(items: Iterable, total: int, desc: str) -> Iterator:
    for index, item in enumerate(items, start=1):
        if index == total or index % 20 == 0:
            print(f"{desc}: {index}/{total}", file=sys.stderr, flush=True)
        yield item
