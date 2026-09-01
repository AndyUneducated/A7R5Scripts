from pathlib import Path

import pytest

from vclip.merge import (
    _check_compatible,
    _default_output,
    _fatal_attrs,
)
from conftest import make_sdr_info


def test_default_output_strips_part_suffix():
    assert _default_output(Path("/x/movie_part001.mp4")) == Path("/x/movie_merged.mp4")
    assert _default_output(Path("/x/clip.mov")) == Path("/x/clip_merged.mov")


def test_fatal_attrs_ignores_path():
    a = make_sdr_info(path=Path("/a.mp4"))
    b = make_sdr_info(path=Path("/b.mp4"))
    assert _fatal_attrs(a) == _fatal_attrs(b)


def test_check_compatible_ok():
    assert _check_compatible([make_sdr_info(), make_sdr_info()]) == []


def test_check_compatible_resolution_mismatch_raises():
    with pytest.raises(ValueError):
        _check_compatible([make_sdr_info(width=1920), make_sdr_info(width=1280)])


def test_check_compatible_fps_mismatch_raises():
    # 帧率不同会导致合并后时长错乱/音画不同步，必须拦截
    with pytest.raises(ValueError, match="帧率"):
        _check_compatible([make_sdr_info(fps=30.0), make_sdr_info(fps=25.0)])


def test_check_compatible_fps_rounding_tolerated():
    # 29.97 的两种表示应视为一致，不误拦
    _check_compatible([make_sdr_info(fps=29.97), make_sdr_info(fps=29.970003)])


def test_check_compatible_sample_rate_mismatch_raises():
    with pytest.raises(ValueError, match="音频采样率"):
        _check_compatible([
            make_sdr_info(audio_sample_rate=48000),
            make_sdr_info(audio_sample_rate=44100),
        ])


def test_check_compatible_channels_mismatch_raises():
    with pytest.raises(ValueError, match="音频声道数"):
        _check_compatible([
            make_sdr_info(audio_channels=2),
            make_sdr_info(audio_channels=6),
        ])


def test_check_compatible_sar_mismatch_raises():
    with pytest.raises(ValueError, match="SAR"):
        _check_compatible([make_sdr_info(sar="1:1"), make_sdr_info(sar="4:3")])


def test_check_compatible_color_mismatch_warns_not_raises():
    # 色彩元数据不一致不阻断，但要返回告警
    warns = _check_compatible([
        make_sdr_info(color_primaries="bt709"),
        make_sdr_info(color_primaries="bt2020"),
    ])
    assert warns and any("色彩" in w for w in warns)
