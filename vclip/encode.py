"""根据 EncodeOptions + 视频信息 + 本机能力，构建 ffmpeg 的编码/滤镜参数。"""
from __future__ import annotations

from dataclasses import dataclass, field, replace

from .capabilities import Capabilities
from .probe import VideoInfo

# 按大小切分时，为吸收码率波动预留的余量（实际体积略小于目标）。
SIZE_SAFETY = 0.95

# 各分辨率下的默认目标码率 (kbps)，仅在“质量模式”未显式指定码率时用作参考。
_DEFAULT_BITRATE = {
    "h264": {2160: 45000, 1440: 20000, 1080: 8000, 720: 4000, 480: 1800},
    "hevc": {2160: 25000, 1440: 12000, 1080: 5000, 720: 2500, 480: 1200},
}

_RES_ALIASES = {
    "4k": 2160, "2160p": 2160, "1440p": 1440, "2k": 1440,
    "1080p": 1080, "fhd": 1080, "720p": 720, "hd": 720, "480p": 480,
}


@dataclass
class EncodeOptions:
    codec: str = "h264"            # h264 | hevc
    encoder: str = "auto"          # auto | hardware | software
    resolution: str | None = None  # '1080p' / '720p' / '4k' / '1920x1080' / None(保持)
    fps: float | None = None       # 目标帧率，None=保持
    crf: int | None = None         # 软件编码质量 (越小越清晰，18~28 常用)
    bitrate_kbps: int | None = None
    audio_bitrate_kbps: int = 128
    audio_copy: bool = False
    hdr: str = "auto"              # auto | sdr | keep
    x26x_preset: str = "medium"    # x264/x265 的 -preset


@dataclass
class EncodePlan:
    vf: str | None                 # -vf 滤镜链
    video_args: list[str]
    audio_args: list[str]
    output_ext: str
    warnings: list[str] = field(default_factory=list)
    video_bitrate_kbps: int | None = None   # 用于按大小切分时估算
    audio_bitrate_kbps: int = 128
    hdr_mode: str = "none"         # none | sdr | keep
    encoder_name: str = ""

    @property
    def total_bitrate_kbps(self) -> int | None:
        if self.video_bitrate_kbps is None:
            return None
        return self.video_bitrate_kbps + self.audio_bitrate_kbps


def parse_resolution(value: str | None) -> tuple[int | None, int | None]:
    """返回 (width, height)。仅给出高度时 width 为 None（按比例缩放）。"""
    if not value:
        return None, None
    v = value.strip().lower()
    if v in _RES_ALIASES:
        return None, _RES_ALIASES[v]
    if "x" in v:
        w, _, h = v.partition("x")
        return int(w), int(h)
    if v.endswith("p") and v[:-1].isdigit():
        return None, int(v[:-1])
    if v.isdigit():
        return None, int(v)
    raise ValueError(f"无法识别的分辨率：{value}")


def _default_bitrate(codec: str, height: int) -> int:
    table = _DEFAULT_BITRATE[codec]
    for h in sorted(table):
        if height <= h:
            return table[h]
    return table[max(table)]


def resolve_hdr_mode(
    info: VideoInfo, opts: EncodeOptions, caps: Capabilities
) -> tuple[str, list[str]]:
    warnings: list[str] = []
    if not info.is_hdr:
        return "none", warnings

    mode = opts.hdr
    if mode == "sdr":
        if not caps.can_tonemap_hdr:
            warnings.append(
                "当前 ffmpeg 缺少 zscale/libplacebo，HDR->SDR 只能做粗略转换，"
                "高光/色彩可能不准。建议改用 --hdr keep，或安装带 libzimg 的 ffmpeg。"
            )
        return "sdr", warnings
    if mode == "keep":
        return "keep", warnings

    # auto
    if caps.can_tonemap_hdr:
        return "sdr", warnings
    warnings.append(
        "源为 HDR 且本机 ffmpeg 无法做高质量 tone-mapping，已自动选择 --hdr keep"
        "（保留 HEVC/HDR）。如需 SDR，请显式加 --hdr sdr（画质会打折）。"
    )
    return "keep", warnings


def _tonemap_filters(caps: Capabilities) -> list[str]:
    """构建 HDR(PQ) -> SDR(bt709) 的滤镜。按可用能力择优。"""
    if caps.libplacebo:
        return [
            "libplacebo=tonemapping=bt.2446a:colorspace=bt709:"
            "color_primaries=bt709:color_trc=bt709:range=tv:format=yuv420p"
        ]
    if caps.zscale and caps.tonemap:
        return [
            "zscale=transfer=linear:npl=100",
            "tonemap=tonemap=hable:desat=0",
            "zscale=primaries=bt709:transfer=bt709:matrix=bt709:range=tv",
            "format=yuv420p",
        ]
    # 兜底：无 tone-mapping 能力，尽力而为（颜色可能失真）
    return ["format=yuv420p"]


def _scale_filter(
    info: VideoInfo, opts: EncodeOptions
) -> tuple[str | None, str]:
    """返回 (滤镜, 说明)。仅缩小不放大。"""
    w, h = parse_resolution(opts.resolution)
    if w is None and h is None:
        return None, "保持原分辨率"
    if w is not None and h is not None:
        return f"scale={w}:{h}", f"缩放到 {w}x{h}"
    # 仅高度：保持宽高比，且不放大
    if h is not None and info.height and h >= info.height:
        return None, f"目标高度 {h} ≥ 源高度，保持原分辨率"
    return f"scale=-2:{h}", f"缩放到高度 {h}（保持宽高比）"


def _resolve_encoder(
    opts: EncodeOptions, caps: Capabilities, hdr_mode: str
) -> tuple[str, bool, list[str]]:
    """返回 (编码器名, 是否硬件, warnings)。"""
    warnings: list[str] = []
    codec = opts.codec
    force_software = opts.encoder == "software" or opts.crf is not None

    use_hw = False
    if not force_software:
        if opts.encoder == "hardware":
            use_hw = True
        elif opts.encoder == "auto":
            if hdr_mode == "keep":
                # 保留 HDR 时软件 x265 能可靠写入 HDR10 元数据，硬件会丢标签
                use_hw = False
                warnings.append(
                    "保留 HDR 已默认用软件 x265（HDR 元数据更可靠，但速度较慢）；"
                    "如需硬件加速可加 --encoder hardware。"
                )
            else:
                # 默认优先硬件（对 4K60 大文件速度优势巨大），除非用户要 CRF 质量
                use_hw = opts.crf is None

    if use_hw:
        hw = caps.hw_encoder(codec)
        if hw:
            if hdr_mode == "keep":
                warnings.append(
                    f"硬件编码器 {hw} 常常不写入 HDR 的 transfer/primaries 标签，"
                    "输出可能不被识别为 HDR。保留 HDR 建议用 --encoder software。"
                )
            return hw, True, warnings
        warnings.append("未找到对应的硬件编码器，回退到软件编码。")

    if codec == "hevc":
        if caps.libx265:
            return "libx265", False, warnings
        raise RuntimeError("本机 ffmpeg 没有 libx265，无法软件编码 HEVC。")
    if caps.libx264:
        return "libx264", False, warnings
    raise RuntimeError("本机 ffmpeg 没有 libx264，无法软件编码 H.264。")


def build_plan(
    info: VideoInfo,
    opts: EncodeOptions,
    caps: Capabilities,
    *,
    force_bitrate: bool = False,
) -> EncodePlan:
    """构建编码计划。force_bitrate=True 时（按大小切分）忽略 CRF，强制用码率。"""
    warnings: list[str] = []
    hdr_mode, w1 = resolve_hdr_mode(info, opts, caps)
    warnings += w1

    # H.264 无法承载 HDR10，保留 HDR 必须用 HEVC。
    if hdr_mode == "keep" and opts.codec != "hevc":
        warnings.append("HDR 保留模式需要 HEVC，已自动把编码切换为 hevc。")
        opts = replace(opts, codec="hevc")

    if force_bitrate and opts.crf is not None:
        opts = replace(opts, crf=None)

    encoder_name, is_hw, w2 = _resolve_encoder(opts, caps, hdr_mode)
    warnings += w2

    # ---- 滤镜链 ----
    filters: list[str] = []
    if hdr_mode == "sdr":
        filters += _tonemap_filters(caps)
    scale, _scale_desc = _scale_filter(info, opts)
    if scale:
        filters.append(scale)
    if opts.fps:
        filters.append(f"fps={opts.fps:g}")
    if hdr_mode == "keep" and not is_hw:
        filters.append("format=yuv420p10le")
    elif hdr_mode != "keep" and "format=yuv420p" not in filters:
        filters.append("format=yuv420p")
    vf = ",".join(filters) if filters else None

    # ---- 视频编码参数 ----
    video_args: list[str] = ["-c:v", encoder_name]
    video_bitrate_kbps: int | None = opts.bitrate_kbps

    # 目标高度（用于默认码率估算）
    _, th = parse_resolution(opts.resolution)
    eff_height = th if th and (not info.height or th < info.height) else info.height

    if is_hw:
        if video_bitrate_kbps is None:
            video_bitrate_kbps = _default_bitrate(opts.codec, eff_height or 1080)
        video_args += [
            "-b:v", f"{video_bitrate_kbps}k",
            "-maxrate", f"{int(video_bitrate_kbps * 1.5)}k",
        ]
    else:
        video_args += ["-preset", opts.x26x_preset]
        if opts.crf is not None:
            video_args += ["-crf", str(opts.crf)]
            # CRF 模式无法精确预测码率，估个值供参考
            video_bitrate_kbps = video_bitrate_kbps or _default_bitrate(
                opts.codec, eff_height or 1080
            )
        else:
            if video_bitrate_kbps is None:
                video_bitrate_kbps = _default_bitrate(opts.codec, eff_height or 1080)
            video_args += [
                "-b:v", f"{video_bitrate_kbps}k",
                "-maxrate", f"{video_bitrate_kbps}k",
                "-bufsize", f"{video_bitrate_kbps * 2}k",
            ]

    # mp4 里的 codec tag：HEVC 必须写 hvc1，否则 QuickTime/Apple 设备拒绝播放
    # （libx265 默认写 hev1）。H.264 默认即 avc1，这里一并显式化，软硬件行为统一。
    video_args += ["-tag:v", "hvc1" if opts.codec == "hevc" else "avc1"]

    # ---- 色彩元数据 ----
    if hdr_mode == "keep":
        video_args += [
            "-color_primaries", "bt2020",
            "-color_trc", "smpte2084",
            "-colorspace", "bt2020nc",
            "-color_range", "tv",
        ]
        if not is_hw:
            video_args += [
                "-pix_fmt", "yuv420p10le",
                "-x265-params",
                "hdr-opt=1:repeat-headers=1:colorprim=bt2020:"
                "transfer=smpte2084:colormatrix=bt2020nc",
            ]
        else:
            video_args += ["-profile:v", "main10"]
    else:
        video_args += [
            "-color_primaries", "bt709",
            "-color_trc", "bt709",
            "-colorspace", "bt709",
            "-color_range", "tv",
        ]

    # ---- 音频 ----
    if opts.audio_copy or not info.has_audio:
        audio_args = ["-c:a", "copy"] if info.has_audio else ["-an"]
        audio_kbps = 0
    else:
        audio_args = ["-c:a", "aac", "-b:a", f"{opts.audio_bitrate_kbps}k"]
        audio_kbps = opts.audio_bitrate_kbps

    return EncodePlan(
        vf=vf,
        video_args=video_args,
        audio_args=audio_args,
        output_ext=".mp4",
        warnings=warnings,
        video_bitrate_kbps=video_bitrate_kbps,
        audio_bitrate_kbps=audio_kbps,
        hdr_mode=hdr_mode,
        encoder_name=encoder_name,
    )
