"""无损重组：把切分后的片段用 ffmpeg concat 复用器拼回一个视频。

严格无损：始终使用 -c copy，绝不重新编码。若各片段的编码参数不一致
（无法做到无损拼接），会直接报错并说明原因，交由用户处理，而不是偷偷转码。
"""
from __future__ import annotations

import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from . import runner
from .inputs import resolve_inputs
from .pipeline import NullReporter, Reporter
from .probe import VideoInfo, format_duration, format_size, probe


def _fatal_attrs(info: VideoInfo) -> dict[str, object]:
    """决定能否无损拼接的**关键**特征。任何一项不同都会破坏拼接结果
    （分辨率/像素格式不符会直接失败；帧率/SAR/音频采样率/声道不符会导致
    时长错乱或音画不同步），因此不一致就必须拒绝，绝不静默转码。
    """
    return {
        "视频编码": info.codec,
        "分辨率": f"{info.width}x{info.height}",
        "像素格式": info.pix_fmt or "?",
        "帧率": round(info.fps, 2),
        "像素宽高比(SAR)": info.sar or "1:1",
        "音频编码": info.audio_codec or "无",
        "音频采样率": info.audio_sample_rate,
        "音频声道数": info.audio_channels,
    }


def _color_attrs(info: VideoInfo) -> dict[str, str]:
    """色彩元数据。不影响像素数据的无损性（流仍逐比特复制），但不一致时
    合并后整段会套用同一组色彩标签，部分片段可能被错误解读，因此单独告警。
    """
    return {
        "color_transfer": info.color_transfer or "",
        "color_primaries": info.color_primaries or "",
        "color_space": info.color_space or "",
        "color_range": info.color_range or "",
    }


@dataclass
class MergePlan:
    """一次无损重组任务的完整描述。"""

    inputs: list[VideoInfo]
    output: Path
    total_bytes: int
    total_duration: float
    warnings: list[str] = field(default_factory=list)

    def describe(self) -> str:
        first = self.inputs[0]
        lines = [
            "  操作      : 无损重组 (-c copy，不重新编码)",
            f"  输入片段  : {len(self.inputs)} 段",
            f"  视频      : {first.codec} {first.width}x{first.height} "
            f"({first.pix_fmt}) @ {first.fps:.3f} fps",
            f"  音频      : {first._audio_human()}",
            f"  总时长    : {format_duration(self.total_duration)}",
            f"  总大小    : ~{format_size(self.total_bytes)}",
            f"  输出文件  : {self.output}",
        ]
        return "\n".join(lines)

    def _concat_line(self, path: Path) -> str:
        # ffmpeg concat 清单格式：file '/绝对/路径'，单引号需转义。
        escaped = str(path.resolve()).replace("'", "'\\''")
        return f"file '{escaped}'"

    def _command(self, list_file: Path) -> list[str]:
        return [
            runner.ffmpeg(), "-y", "-hide_banner",
            "-f", "concat", "-safe", "0",
            "-i", str(list_file),
            # -map 0 保留全部流（多音轨/字幕），无损重组应原样拼接。
            "-map", "0",
            "-c", "copy",
            *runner.faststart_args(self.output),
            str(self.output),
        ]

    def execute(
        self, *, dry_run: bool = False, reporter: Reporter | None = None
    ) -> list[Path]:
        reporter = reporter or NullReporter()
        self.output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w", suffix=".txt", delete=False,
            dir=self.output.parent, encoding="utf-8",
        ) as fh:
            list_file = Path(fh.name)
            fh.write("\n".join(self._concat_line(i.path) for i in self.inputs) + "\n")

        cmd = self._command(list_file)
        reporter.command(cmd)
        try:
            if dry_run:
                return []
            proc = runner.run(cmd)
            if proc.returncode != 0:
                raise RuntimeError(f"ffmpeg 执行失败 (exit={proc.returncode})")
        finally:
            list_file.unlink(missing_ok=True)

        return [self.output] if self.output.exists() else []


def _default_output(first: Path) -> Path:
    """从第一个片段推断输出文件名，去掉 _partNNN 后缀，保留原容器扩展名。"""
    stem = first.stem
    m = re.match(r"(.+)_part\d+$", stem)
    base = m.group(1) if m else stem
    return first.parent / f"{base}_merged{first.suffix}"


def _check_compatible(infos: list[VideoInfo]) -> list[str]:
    """校验所有片段能否无损拼接。

    - 关键特征不一致：直接报错终止（绝不转码）。
    - 色彩元数据不一致：不阻断，返回告警。
    """
    ref = _fatal_attrs(infos[0])
    differing = [
        key for key in ref
        if any(_fatal_attrs(i)[key] != ref[key] for i in infos)
    ]

    if differing:
        keys = "、".join(differing)
        lines = [
            f"无法无损拼接：以下片段的关键参数不一致（差异项：{keys}）。",
            "为保证严格无损，已终止（不会自动转码）。",
            "这些差异会导致拼接失败或音画不同步，必须先统一参数再重组。",
            "",
            "各片段情况（★ 标记与第一段不同的项）：",
        ]
        for info in infos:
            attrs = _fatal_attrs(info)
            parts = []
            for key in ref:
                mark = "★" if attrs[key] != ref[key] else " "
                parts.append(f"{mark}{key}={attrs[key]}")
            lines.append(f"  {info.path.name}:")
            lines.append("      " + "  ".join(parts))
        raise ValueError("\n".join(lines))

    # 关键项一致，检查色彩元数据（软告警）。
    color_ref = _color_attrs(infos[0])
    color_diff = [
        key for key in color_ref
        if any(_color_attrs(i)[key] != color_ref[key] for i in infos)
    ]
    if color_diff:
        return [
            f"注意：各片段的色彩元数据不一致（{', '.join(color_diff)}）。"
            "视频数据仍逐比特无损拼接，但合并后会统一采用第一段的色彩标签，"
            "部分片段的颜色可能被播放器错误解读。若颜色重要请先确认。"
        ]
    return []


def plan_merge(
    inputs: list[str],
    output: str | Path | None = None,
) -> MergePlan:
    files = resolve_inputs(inputs)
    if len(files) < 2:
        raise ValueError("至少需要 2 个片段才能重组")

    infos = [probe(f) for f in files]
    color_warnings = _check_compatible(infos)

    out = Path(output) if output else _default_output(files[0])
    resolved_out = out.resolve()
    if any(f.resolve() == resolved_out for f in files):
        raise ValueError(
            f"输出文件 {out} 与某个输入片段相同，会覆盖输入。请用 -o 指定别的输出路径。"
        )
    total_bytes = sum(i.size_bytes for i in infos)
    total_duration = sum(i.duration for i in infos)

    warnings = [
        "重组为无损拼接：直接复制流，不重新编码，画质/HDR 完全保留。",
    ]
    warnings += color_warnings
    if out.suffix.lower() != files[0].suffix.lower():
        warnings.append(
            f"输出容器 {out.suffix} 与源片段 {files[0].suffix} 不同，"
            "无损拼接建议保持相同容器，否则可能失败。"
        )

    return MergePlan(
        inputs=infos,
        output=out,
        total_bytes=total_bytes,
        total_duration=total_duration,
        warnings=warnings,
    )
