from pathlib import Path

import pytest

from vclip.inputs import natural_key, resolve_inputs


def test_natural_key_orders_numerically():
    names = ["clip_part10.mp4", "clip_part2.mp4", "clip_part1.mp4"]
    paths = [Path(n) for n in names]
    ordered = sorted(paths, key=natural_key)
    assert [p.name for p in ordered] == [
        "clip_part1.mp4", "clip_part2.mp4", "clip_part10.mp4"
    ]


def _touch(p: Path):
    p.write_bytes(b"x")


def test_resolve_inputs_dir_natural_sort_and_excludes_merged(tmp_path):
    for name in ["m_part1.mp4", "m_part2.mp4", "m_part10.mp4", "m_merged.mp4"]:
        _touch(tmp_path / name)
    files = resolve_inputs([str(tmp_path)])
    names = [f.name for f in files]
    assert names == ["m_part1.mp4", "m_part2.mp4", "m_part10.mp4"]
    assert "m_merged.mp4" not in names


def test_resolve_inputs_empty_dir_raises(tmp_path):
    with pytest.raises(ValueError):
        resolve_inputs([str(tmp_path)])


def test_resolve_inputs_missing_file_raises(tmp_path):
    with pytest.raises(ValueError):
        resolve_inputs([str(tmp_path / "nope.mp4")])


def test_resolve_inputs_keeps_explicit_order(tmp_path):
    a, b = tmp_path / "b.mp4", tmp_path / "a.mp4"
    _touch(a)
    _touch(b)
    files = resolve_inputs([str(a), str(b)])
    assert [f.name for f in files] == ["b.mp4", "a.mp4"]
