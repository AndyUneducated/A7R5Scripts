from pathlib import Path

import pytest

from vclip.split import (
    _copy_cmd,
    _encode_seg_cmd,
    plan_duration,
    plan_shrink,
    plan_size,
    plan_trim,
)
from vclip.encode import build_plan
from conftest import make_caps, make_info, make_opts, make_sdr_info


def test_copy_cmd_preserves_all_streams(tmp_path):
    info = make_sdr_info()
    cmd = _copy_cmd(info, 10.0, tmp_path / "out_%03d.mp4")
    # -map 0 保留全部流
    assert "-map" in cmd
    assert cmd[cmd.index("-map") + 1] == "0"
    assert "copy" in cmd
    assert "segment" in cmd


def test_encode_seg_maps_all_audio(tmp_path):
    info = make_sdr_info()
    plan = build_plan(info, make_opts(encoder="software"), make_caps())
    cmd = _encode_seg_cmd(info, plan, 0.0, 10.0, tmp_path / "p.mp4")
    assert "0:v:0" in cmd
    assert "0:a?" in cmd


def test_plan_duration_copy():
    plan = plan_duration(make_sdr_info(duration=120), 30, make_caps(),
                         make_opts(), transcode=False)
    assert plan.mode == "duration-copy"
    assert plan.estimated_parts == 4
    assert len(plan.commands) == 1
    assert plan.encode_plan is None


def test_lossless_duration_split_preserves_source_container():
    # 源为 mkv 时，无损切分应保留 mkv 容器（而非强行改成 mp4）
    info = make_sdr_info(path=Path("/tmp/movie.mkv"), duration=120)
    plan = plan_duration(info, 30, make_caps(), make_opts(), transcode=False)
    assert plan.output_glob.endswith(".mkv")
    assert str(plan.commands[0][-1]).endswith(".mkv")


def test_lossless_size_split_preserves_source_container():
    info = make_sdr_info(path=Path("/tmp/movie.webm"),
                         size_bytes=1000 * 1024 * 1024, duration=100)
    plan = plan_size(info, 200, make_caps(), make_opts(), lossless=True)
    assert plan.output_glob.endswith(".webm")
    assert str(plan.commands[0][-1]).endswith(".webm")


def test_transcode_split_always_outputs_mp4():
    # 转码统一输出 mp4（重新封装），与源容器无关
    info = make_sdr_info(path=Path("/tmp/movie.mkv"), duration=60)
    plan = plan_duration(info, 30, make_caps(),
                         make_opts(encoder="software"), transcode=True)
    assert plan.output_glob.endswith(".mp4")


def test_lossless_copy_has_no_movflags_for_mkv():
    # 无损切分 mkv 时不应带 mp4 专属的 -movflags（trim-copy 场景）
    info = make_sdr_info(path=Path("/tmp/movie.mkv"), duration=60)
    plan = plan_trim(info, 0, 30, make_caps(), make_opts(),
                     transcode=False, outdir="/tmp/out.mkv")
    assert "-movflags" not in plan.commands[0]


def test_lossless_copy_keeps_movflags_for_mp4():
    info = make_sdr_info(path=Path("/tmp/movie.mp4"), duration=60)
    plan = plan_trim(info, 0, 30, make_caps(), make_opts(),
                     transcode=False, outdir="/tmp/out.mp4")
    assert "-movflags" in plan.commands[0]


def test_plan_shrink_default_720p_single_file():
    info = make_sdr_info(path=Path("/tmp/movie.mp4"), width=1920, height=1080,
                         duration=100)
    plan = plan_shrink(info, make_caps(),
                       make_opts(resolution="720p", encoder="software"))
    assert plan.mode == "shrink"
    assert len(plan.commands) == 1
    assert plan.output_files[0].name == "movie_720p.mp4"
    assert "scale=-2:720" in (plan.encode_plan.vf or "")


def test_plan_shrink_never_upscales_label():
    # 源 480p，要求 720p → 只缩小不放大，标签用实际高度 480p
    info = make_sdr_info(path=Path("/tmp/movie.mp4"), width=854, height=480,
                         duration=100)
    plan = plan_shrink(info, make_caps(),
                       make_opts(resolution="720p", encoder="software"))
    assert plan.output_files[0].name == "movie_480p.mp4"


def test_plan_shrink_target_mb_sets_bitrate():
    info = make_sdr_info(path=Path("/tmp/movie.mp4"), width=1920, height=1080,
                         duration=100)
    plan = plan_shrink(info, make_caps(), make_opts(encoder="software"),
                       target_mb=50)
    # 50MB / 100s ≈ 4Mbps 总，视频码率应据此设置（减去音频 128k）
    assert plan.encode_plan.video_bitrate_kbps is not None
    assert "-b:v" in plan.encode_plan.video_args


def test_plan_shrink_rejects_bad_target():
    info = make_sdr_info(duration=100)
    with pytest.raises(ValueError):
        plan_shrink(info, make_caps(), make_opts(), target_mb=0)


def test_plan_duration_encode_parts_and_jobs():
    plan = plan_duration(make_sdr_info(duration=100), 30, make_caps(),
                         make_opts(encoder="software"), transcode=True, jobs=4)
    assert plan.mode == "duration-encode"
    assert plan.estimated_parts == 4  # ceil(100/30)
    assert len(plan.commands) == 4
    assert plan.jobs == 4


def test_plan_duration_rejects_nonpositive():
    with pytest.raises(ValueError):
        plan_duration(make_sdr_info(), 0, make_caps(), make_opts(), transcode=False)


def test_plan_size_copy_requires_bitrate():
    with pytest.raises(ValueError):
        plan_size(make_sdr_info(duration=0, size_bytes=0, stream_bitrate=0),
                  100, make_caps(), make_opts(), lossless=True)


def test_plan_size_copy_segments():
    info = make_sdr_info(size_bytes=1000 * 1024 * 1024, duration=100)
    plan = plan_size(info, 200, make_caps(), make_opts(), lossless=True)
    assert plan.mode == "size-copy"
    assert plan.estimated_parts >= 1


def test_plan_size_rejects_nonpositive():
    with pytest.raises(ValueError):
        plan_size(make_sdr_info(), 0, make_caps(), make_opts(), lossless=False)


def test_plan_trim_copy_basic(tmp_path):
    info = make_sdr_info(duration=100)
    plan = plan_trim(info, 10, 40, make_caps(), make_opts(),
                     transcode=False, outdir=tmp_path)
    assert plan.mode == "trim-copy"
    assert plan.estimated_parts == 1
    cmd = plan.commands[0]
    assert "-ss" in cmd and cmd[cmd.index("-ss") + 1] == "10.000"
    assert "-t" in cmd


def test_plan_trim_to_end_omits_t(tmp_path):
    info = make_sdr_info(duration=100)
    plan = plan_trim(info, 90, None, make_caps(), make_opts(),
                     transcode=False, outdir=tmp_path)
    assert "-t" not in plan.commands[0]


def test_plan_trim_validation():
    info = make_sdr_info(duration=100)
    with pytest.raises(ValueError):
        plan_trim(info, -1, 10, make_caps(), make_opts(), transcode=False)
    with pytest.raises(ValueError):
        plan_trim(info, 200, None, make_caps(), make_opts(), transcode=False)
    with pytest.raises(ValueError):
        plan_trim(info, 30, 20, make_caps(), make_opts(), transcode=False)


def test_plan_trim_encode(tmp_path):
    info = make_sdr_info(duration=100)
    plan = plan_trim(info, 10, 40, make_caps(),
                     make_opts(encoder="software"), transcode=True, outdir=tmp_path)
    assert plan.mode == "trim-encode"
    assert plan.encode_plan is not None
