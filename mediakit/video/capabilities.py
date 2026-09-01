"""检测本机 ffmpeg 支持哪些编码器 / 滤镜。

不同的 ffmpeg 编译版本能力差异很大（尤其是 HDR->SDR 需要的 zscale /
libplacebo），所以运行时探测一次并缓存，据此选择安全的默认行为。

硬件编码器按平台差异很大：
  - Apple（macOS）  : videotoolbox
  - NVIDIA          : nvenc
  - Intel           : qsv
  - Windows/AMD     : amf
  - Linux 通用      : vaapi（需要 -vaapi_device，plug-and-play 程度低，仅记录不自动选）
"""
from __future__ import annotations

import functools
from dataclasses import asdict, dataclass

from . import runner


def _run(args: list[str]) -> str:
    return runner.run(args, capture=True).stdout or ""


def _names(text: str) -> set[str]:
    """从 `ffmpeg -encoders`/`-filters` 输出里提取"名称列"。

    每个能力行形如 `flags  name  描述…`，名字是第二列。用精确的集合成员判断，
    避免子串误报（如 `tonemap` 命中 `tonemap_opencl`、`libx264` 命中 `libx264rgb`）。
    """
    names: set[str] = set()
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and not parts[0].endswith(":"):
            names.add(parts[1])
    return names


@dataclass(frozen=True)
class Capabilities:
    libx264: bool
    libx265: bool
    # ---- 硬件编码器 ----
    vt_h264: bool          # h264_videotoolbox (Apple)
    vt_hevc: bool          # hevc_videotoolbox
    nvenc_h264: bool       # h264_nvenc (NVIDIA)
    nvenc_hevc: bool       # hevc_nvenc
    qsv_h264: bool         # h264_qsv (Intel Quick Sync)
    qsv_hevc: bool         # hevc_qsv
    amf_h264: bool         # h264_amf (AMD, 主要 Windows)
    amf_hevc: bool         # hevc_amf
    vaapi_h264: bool       # h264_vaapi (Linux VAAPI)
    vaapi_hevc: bool       # hevc_vaapi
    # ---- 滤镜 ----
    zscale: bool
    libplacebo: bool
    tonemap: bool
    colorspace: bool

    @property
    def has_hardware(self) -> bool:
        return any((
            self.vt_h264, self.vt_hevc,
            self.nvenc_h264, self.nvenc_hevc,
            self.qsv_h264, self.qsv_hevc,
            self.amf_h264, self.amf_hevc,
        ))

    def hw_encoder(self, codec: str) -> str | None:
        """返回该编码格式下可用的硬件编码器名（按平台优先级），没有则 None。

        优先级：videotoolbox > nvenc > qsv > amf。vaapi 因需显式设备，不参与自动选择。
        """
        order = {
            "h264": [
                ("h264_videotoolbox", self.vt_h264),
                ("h264_nvenc", self.nvenc_h264),
                ("h264_qsv", self.qsv_h264),
                ("h264_amf", self.amf_h264),
            ],
            "hevc": [
                ("hevc_videotoolbox", self.vt_hevc),
                ("hevc_nvenc", self.nvenc_hevc),
                ("hevc_qsv", self.qsv_hevc),
                ("hevc_amf", self.amf_hevc),
            ],
        }.get(codec, [])
        for name, ok in order:
            if ok:
                return name
        return None

    @property
    def can_tonemap_hdr(self) -> bool:
        """是否具备高质量 HDR->SDR 色调映射能力。"""
        return self.libplacebo or (self.zscale and self.tonemap)

    def as_dict(self) -> dict:
        """结构化输出（供 `caps --json` / 脚本集成）。"""
        d = asdict(self)
        d["auto_h264"] = self.hw_encoder("h264") or ("libx264" if self.libx264 else None)
        d["auto_hevc"] = self.hw_encoder("hevc") or ("libx265" if self.libx265 else None)
        d["can_tonemap_hdr"] = self.can_tonemap_hdr
        return d

    def human(self) -> str:
        def yn(b: bool) -> str:
            return "✓" if b else "—"

        auto_h264 = self.hw_encoder("h264") or ("libx264" if self.libx264 else "不可用")
        auto_hevc = self.hw_encoder("hevc") or ("libx265" if self.libx265 else "不可用")
        return "\n".join([
            "本机 ffmpeg 能力：",
            f"  软件编码  : H.264 {yn(self.libx264)}    HEVC {yn(self.libx265)}",
            f"  硬件 H.264: videotoolbox {yn(self.vt_h264)}  nvenc {yn(self.nvenc_h264)}"
            f"  qsv {yn(self.qsv_h264)}  amf {yn(self.amf_h264)}  vaapi {yn(self.vaapi_h264)}",
            f"  硬件 HEVC : videotoolbox {yn(self.vt_hevc)}  nvenc {yn(self.nvenc_hevc)}"
            f"  qsv {yn(self.qsv_hevc)}  amf {yn(self.amf_hevc)}  vaapi {yn(self.vaapi_hevc)}",
            f"  自动选用  : H.264 → {auto_h264}    HEVC → {auto_hevc}",
            f"  HDR→SDR   : {'可高质量 tone-mapping' if self.can_tonemap_hdr else '不可（缺 zscale/libplacebo）'}"
            f"  (zscale {yn(self.zscale)}, libplacebo {yn(self.libplacebo)},"
            f" tonemap {yn(self.tonemap)}, colorspace {yn(self.colorspace)})",
        ])


@functools.lru_cache(maxsize=1)
def detect() -> Capabilities:
    enc_names = _names(_run([runner.ffmpeg(), "-hide_banner", "-encoders"]))
    flt_names = _names(_run([runner.ffmpeg(), "-hide_banner", "-filters"]))

    def enc(name: str) -> bool:
        return name in enc_names

    def flt(name: str) -> bool:
        return name in flt_names

    return Capabilities(
        libx264=enc("libx264"),
        libx265=enc("libx265"),
        vt_h264=enc("h264_videotoolbox"),
        vt_hevc=enc("hevc_videotoolbox"),
        nvenc_h264=enc("h264_nvenc"),
        nvenc_hevc=enc("hevc_nvenc"),
        qsv_h264=enc("h264_qsv"),
        qsv_hevc=enc("hevc_qsv"),
        amf_h264=enc("h264_amf"),
        amf_hevc=enc("hevc_amf"),
        vaapi_h264=enc("h264_vaapi"),
        vaapi_hevc=enc("hevc_vaapi"),
        zscale=flt("zscale"),
        libplacebo=flt("libplacebo"),
        tonemap=flt("tonemap"),
        colorspace=flt("colorspace"),
    )
