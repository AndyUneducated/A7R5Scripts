"""用 ffprobe 读取视频信息。"""
from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

from . import runner

HDR_TRANSFERS = {"smpte2084", "arib-std-b67"}  # PQ / HLG


class ProbeError(RuntimeError):
    pass


def _require(tool: str) -> str:
    try:
        return runner.tool_path(tool)
    except runner.FFmpegNotFound as exc:
        raise ProbeError(str(exc)) from exc


def _fraction(value: str | None) -> float:
    """把 '60000/1001' 之类的分数字符串转成 float。"""
    if not value:
        return 0.0
    value = value.strip()
    if "/" in value:
        num, _, den = value.partition("/")
        try:
            den_f = float(den)
            return float(num) / den_f if den_f else 0.0
        except ValueError:
            return 0.0
    try:
        return float(value)
    except ValueError:
        return 0.0


@dataclass
class VideoInfo:
    path: Path
    duration: float          # 秒
    size_bytes: int
    width: int
    height: int
    codec: str
    pix_fmt: str
    color_transfer: str
    color_primaries: str
    color_space: str
    color_range: str
    sar: str                 # 像素宽高比 sample_aspect_ratio，如 "1:1"
    fps: float
    stream_bitrate: int      # 视频流码率 (bps)，可能为 0
    has_audio: bool
    audio_codec: str | None
    audio_sample_rate: int   # 音频采样率 (Hz)，无音频为 0
    audio_channels: int      # 音频声道数，无音频为 0

    @property
    def is_hdr(self) -> bool:
        return (
            self.color_transfer in HDR_TRANSFERS
            or self.color_primaries == "bt2020"
            or self.color_space.startswith("bt2020")
        )

    @property
    def is_10bit(self) -> bool:
        return "10" in self.pix_fmt or "p010" in self.pix_fmt

    @property
    def overall_bitrate_bps(self) -> int:
        """按文件大小/时长估算的整体码率，最稳妥的估算方式。"""
        if self.duration > 0:
            return int(self.size_bytes * 8 / self.duration)
        return self.stream_bitrate

    def as_dict(self) -> dict:
        """结构化输出（供 `info --json` / 脚本集成）。"""
        d = asdict(self)
        d["path"] = str(self.path)
        d["is_hdr"] = self.is_hdr
        d["is_10bit"] = self.is_10bit
        d["overall_bitrate_bps"] = self.overall_bitrate_bps
        return d

    def _audio_human(self) -> str:
        if not self.has_audio:
            return "无"
        parts = [self.audio_codec or "?"]
        if self.audio_sample_rate:
            parts.append(f"{self.audio_sample_rate} Hz")
        if self.audio_channels:
            parts.append(f"{self.audio_channels}ch")
        return " ".join(parts)

    def human(self) -> str:
        hdr = "HDR" if self.is_hdr else "SDR"
        depth = "10bit" if self.is_10bit else "8bit"
        return (
            f"{self.path.name}\n"
            f"  时长      : {format_duration(self.duration)}\n"
            f"  分辨率    : {self.width}x{self.height} @ {self.fps:.3f} fps\n"
            f"  视频编码  : {self.codec} ({self.pix_fmt}, {depth})\n"
            f"  动态范围  : {hdr}"
            f" (trc={self.color_transfer or '?'}, prim={self.color_primaries or '?'})\n"
            f"  像素宽高比: {self.sar or '?'}\n"
            f"  音频      : {self._audio_human()}\n"
            f"  文件大小  : {format_size(self.size_bytes)}\n"
            f"  整体码率  : {self.overall_bitrate_bps / 1_000_000:.1f} Mbps"
        )


def probe(path: str | Path) -> VideoInfo:
    ffprobe = _require("ffprobe")
    p = Path(path)
    if not p.exists():
        raise ProbeError(f"文件不存在：{p}")

    cmd = [
        ffprobe, "-v", "error",
        "-print_format", "json",
        "-show_format", "-show_streams",
        str(p),
    ]
    try:
        out = runner.run(cmd, capture=True, check=True).stdout
    except subprocess.CalledProcessError as exc:  # pragma: no cover
        raise ProbeError(f"ffprobe 读取失败：{exc.stderr.strip()}") from exc

    data = json.loads(out)
    streams = data.get("streams", [])
    fmt = data.get("format", {})

    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if v is None:
        raise ProbeError(f"{p.name} 里找不到视频流")

    duration = _fraction(v.get("duration") or fmt.get("duration"))
    size_bytes = int(fmt.get("size") or (p.stat().st_size))
    fps = _fraction(v.get("avg_frame_rate")) or _fraction(v.get("r_frame_rate"))

    sar = v.get("sample_aspect_ratio", "") or ""
    if sar in ("0:1", "0:0"):  # ffprobe 对未知 SAR 的表示，视为未标注
        sar = ""

    return VideoInfo(
        path=p,
        duration=duration,
        size_bytes=size_bytes,
        width=int(v.get("width") or 0),
        height=int(v.get("height") or 0),
        codec=v.get("codec_name", "?"),
        pix_fmt=v.get("pix_fmt", ""),
        color_transfer=v.get("color_transfer", ""),
        color_primaries=v.get("color_primaries", ""),
        color_space=v.get("color_space", ""),
        color_range=v.get("color_range", ""),
        sar=sar,
        fps=fps,
        stream_bitrate=int(_fraction(v.get("bit_rate")) or 0),
        has_audio=a is not None,
        audio_codec=a.get("codec_name") if a else None,
        audio_sample_rate=int(_fraction(a.get("sample_rate")) if a else 0),
        audio_channels=int(a.get("channels") or 0) if a else 0,
    )


def format_duration(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h:d}:{m:02d}:{s:02d}"
    return f"{m:d}:{s:02d}"


def format_size(num_bytes: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if num_bytes < 1024 or unit == "TB":
            return f"{num_bytes:.1f} {unit}" if unit != "B" else f"{int(num_bytes)} B"
        num_bytes /= 1024
    return f"{num_bytes:.1f} TB"
