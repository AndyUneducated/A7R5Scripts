"""测试公共夹具与工厂。

通过 VCLIP_FFMPEG / VCLIP_FFPROBE 环境变量把二进制路径固定成占位字符串，
让"构建命令 / 计划"类测试完全不依赖本机是否安装 ffmpeg（这些测试只检查
生成的参数，不真正执行）。
"""
from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("VCLIP_FFMPEG", "ffmpeg")
os.environ.setdefault("VCLIP_FFPROBE", "ffprobe")

from vclip.capabilities import Capabilities  # noqa: E402
from vclip.encode import EncodeOptions  # noqa: E402
from vclip.probe import VideoInfo  # noqa: E402


def make_info(**kw) -> VideoInfo:
    defaults = dict(
        path=Path("/tmp/movie.mp4"),
        duration=120.0,
        size_bytes=120_000_000,
        width=3840,
        height=2160,
        codec="hevc",
        pix_fmt="yuv420p10le",
        color_transfer="smpte2084",
        color_primaries="bt2020",
        color_space="bt2020nc",
        color_range="tv",
        sar="1:1",
        fps=60.0,
        stream_bitrate=0,
        has_audio=True,
        audio_codec="aac",
        audio_sample_rate=48000,
        audio_channels=2,
    )
    defaults.update(kw)
    return VideoInfo(**defaults)


def make_sdr_info(**kw) -> VideoInfo:
    base = dict(
        codec="h264",
        pix_fmt="yuv420p",
        color_transfer="bt709",
        color_primaries="bt709",
        color_space="bt709",
        width=1920,
        height=1080,
        fps=30.0,
    )
    base.update(kw)
    return make_info(**base)


def make_caps(**kw) -> Capabilities:
    defaults = dict(
        libx264=True,
        libx265=True,
        vt_h264=False,
        vt_hevc=False,
        nvenc_h264=False,
        nvenc_hevc=False,
        qsv_h264=False,
        qsv_hevc=False,
        amf_h264=False,
        amf_hevc=False,
        vaapi_h264=False,
        vaapi_hevc=False,
        zscale=True,
        libplacebo=False,
        tonemap=True,
        colorspace=True,
    )
    defaults.update(kw)
    return Capabilities(**defaults)


def make_opts(**kw) -> EncodeOptions:
    return EncodeOptions(**kw)
