"""切分核心逻辑：按时长切、按大小切（无损或转码）、裁剪单个子片段。

- 无损(copy)：用 ffmpeg segment 复用器一次切好；切点只能落在关键帧上，
  单段时长/大小会有波动（这是 -c copy 的固有限制）。
- 转码(encode)：逐段用 -ss/-t 精确编码，段数/时长可预测，且对任意编码器都可靠。
  多段可用 jobs>1 并行编码。
"""
from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from pathlib import Path

from . import runner
from .capabilities import Capabilities
from .encode import (
    SIZE_SAFETY,
    EncodeOptions,
    EncodePlan,
    build_plan,
    parse_resolution,
)
from .pipeline import NullReporter, Reporter
from .probe import VideoInfo, format_duration, format_size


def _copy_split_warnings(info: VideoInfo, base: str) -> list[str]:
    """无损切分（-c copy）的固有风险提示。"""
    warns = [base]
    # open-GOP（HEVC/x265 默认）在切点处的前向引用帧无法跨段解码，
    # 可能导致边界丢帧。closed-GOP（多数 H.264）无此问题。
    if info.codec in ("hevc", "h265"):
        warns.append(
            "该视频是 HEVC：若为 open-GOP 编码（x265 默认），无损切分可能在"
            "段边界丢失少量帧。需要逐帧精确请改用 --transcode，或切分后核对帧数。"
        )
    return warns


@dataclass
class SplitPlan:
    """一次切分任务的完整描述（可在真正执行前打印确认）。"""
    mode: str                       # duration-copy | duration-encode | size-copy | size-encode | trim-*
    segment_seconds: float
    estimated_parts: int
    estimated_part_size: int        # bytes
    commands: list[list[str]]       # 需要执行的一条或多条 ffmpeg 命令
    output_dir: Path
    output_files: list[Path] | None  # 转码模式：明确的输出文件；copy 模式为 None
    output_glob: str                 # copy 模式：执行后用于收集文件的 glob
    encode_plan: EncodePlan | None
    warnings: list[str] = field(default_factory=list)
    jobs: int = 1                    # 转码多段时的并行度

    def describe(self) -> str:
        if self.mode == "shrink":
            lines = [
                "  操作      : 整片压缩为单个文件",
                f"  时长      : {format_duration(self.segment_seconds)}",
                f"  预计大小  : ~{format_size(self.estimated_part_size)}",
                f"  输出文件  : {self.output_files[0] if self.output_files else self.output_dir}",
            ]
        else:
            lines = [
                f"  切分模式  : {self.mode}",
                f"  每段时长  : {format_duration(self.segment_seconds)}",
                f"  预计段数  : {self.estimated_parts} 段",
                f"  单段大小  : ~{format_size(self.estimated_part_size)}",
                f"  输出目录  : {self.output_dir}",
            ]
        if self.encode_plan:
            lines.append(f"  编码器    : {self.encode_plan.encoder_name}")
            lines.append(f"  HDR 处理  : {self.encode_plan.hdr_mode}")
            if self.jobs > 1 and len(self.commands) > 1:
                lines.append(f"  并行度    : {self.jobs} 段同时编码")
            if self.encode_plan.vf:
                lines.append(f"  滤镜      : {self.encode_plan.vf}")
        return "\n".join(lines)

    def _run_serial(self, reporter: Reporter) -> None:
        total = len(self.commands)
        for i, cmd in enumerate(self.commands, 1):
            reporter.segment_start(i, total)
            proc = runner.run(cmd)
            if proc.returncode != 0:
                raise RuntimeError(f"ffmpeg 执行失败 (exit={proc.returncode})")

    def _run_parallel(self, jobs: int, reporter: Reporter) -> None:
        total = len(self.commands)
        reporter.note(f"并行编码：{jobs} 段同时进行，共 {total} 段 …")

        def _one(idx_cmd):
            idx, cmd = idx_cmd
            proc = runner.run(cmd, capture=True)
            return idx, proc.returncode, proc.stderr or ""

        with ThreadPoolExecutor(max_workers=jobs) as ex:
            for idx, rc, err in ex.map(_one, enumerate(self.commands, 1)):
                if rc != 0:
                    raise RuntimeError(
                        f"第 {idx}/{total} 段 ffmpeg 执行失败 (exit={rc})\n{err.strip()}"
                    )
                reporter.segment_done(idx, total)

    def execute(
        self, *, dry_run: bool = False, reporter: Reporter | None = None
    ) -> list[Path]:
        """执行切分。返回生成的文件列表；进度通过 reporter 上报。"""
        reporter = reporter or NullReporter()
        total = len(self.commands)
        for i, cmd in enumerate(self.commands, 1):
            reporter.command(cmd, i, total)
        if dry_run:
            return []

        self.output_dir.mkdir(parents=True, exist_ok=True)
        if self.jobs > 1 and total > 1:
            self._run_parallel(self.jobs, reporter)
        else:
            self._run_serial(reporter)

        if self.output_files is not None:
            return [f for f in self.output_files if f.exists()]
        return sorted(self.output_dir.glob(self.output_glob))


def _output_dir(info: VideoInfo, outdir: str | Path | None) -> Path:
    if outdir:
        return Path(outdir)
    return info.path.parent / f"{info.path.stem}_clips"


def _part_path(info: VideoInfo, out_dir: Path, idx: int, ext: str) -> Path:
    return out_dir / f"{info.path.stem}_part{idx:03d}{ext}"


def _pattern(info: VideoInfo, out_dir: Path, ext: str) -> Path:
    return out_dir / f"{info.path.stem}_part%03d{ext}"


def _copy_ext(info: VideoInfo) -> str:
    """无损切分的分段容器：保留源容器扩展名。

    强行统一成 .mp4 会在源为 mkv/webm/ts 等时出错（`-map 0` 会把字幕/数据流
    一并复制，而这些流未必兼容 mp4）。保留原容器最安全，也最贴合"无损"。
    """
    return info.path.suffix or ".mp4"


def _copy_cmd(info: VideoInfo, seconds: float, pattern: Path) -> list[str]:
    return [
        runner.ffmpeg(), "-y", "-hide_banner", "-i", str(info.path),
        # -map 0 保留全部流（多音轨/字幕等），无损切分应尽量原样保留。
        "-map", "0", "-c", "copy",
        "-f", "segment",
        "-segment_time", f"{seconds:.3f}",
        "-reset_timestamps", "1",
        "-segment_start_number", "1",
        str(pattern),
    ]


def _encode_seg_cmd(
    info: VideoInfo, plan: EncodePlan, start: float, dur: float, outfile: Path
) -> list[str]:
    cmd = [
        runner.ffmpeg(), "-y", "-hide_banner",
        "-ss", f"{start:.3f}", "-i", str(info.path), "-t", f"{dur:.3f}",
        # 视频取首条，音频保留全部（多语言音轨）。
        "-map", "0:v:0", "-map", "0:a?",
    ]
    if plan.vf:
        cmd += ["-vf", plan.vf]
    cmd += plan.video_args + plan.audio_args
    cmd += [*runner.faststart_args(outfile), str(outfile)]
    return cmd


def _encode_commands(
    info: VideoInfo, plan: EncodePlan, seconds: float, out_dir: Path
) -> tuple[list[list[str]], list[Path]]:
    n = max(1, math.ceil(info.duration / seconds))
    cmds: list[list[str]] = []
    files: list[Path] = []
    for i in range(n):
        start = i * seconds
        dur = min(seconds, info.duration - start)
        if dur <= 0:
            break
        out = _part_path(info, out_dir, i + 1, plan.output_ext)
        cmds.append(_encode_seg_cmd(info, plan, start, dur, out))
        files.append(out)
    return cmds, files


def plan_duration(
    info: VideoInfo,
    seconds: float,
    caps: Capabilities,
    opts: EncodeOptions,
    *,
    transcode: bool,
    outdir: str | Path | None = None,
    jobs: int = 1,
) -> SplitPlan:
    if seconds <= 0:
        raise ValueError("每段时长必须大于 0")
    out_dir = _output_dir(info, outdir)
    est_parts = max(1, math.ceil(info.duration / seconds))

    if not transcode:
        ext = _copy_ext(info)
        pattern = _pattern(info, out_dir, ext)
        est_size = int(info.overall_bitrate_bps * seconds / 8)
        return SplitPlan(
            mode="duration-copy",
            segment_seconds=seconds,
            estimated_parts=est_parts,
            estimated_part_size=est_size,
            commands=[_copy_cmd(info, seconds, pattern)],
            output_dir=out_dir,
            output_files=None,
            output_glob=f"{info.path.stem}_part*{ext}",
            encode_plan=None,
            warnings=_copy_split_warnings(
                info, "无损切分：切点只能落在关键帧上，单段实际时长会略有出入。"
            ),
        )

    plan = build_plan(info, opts, caps)
    cmds, files = _encode_commands(info, plan, seconds, out_dir)
    total_kbps = plan.total_bitrate_kbps or info.overall_bitrate_bps // 1000
    est_size = int(total_kbps * 1000 * seconds / 8)
    return SplitPlan(
        mode="duration-encode",
        segment_seconds=seconds,
        estimated_parts=len(files),
        estimated_part_size=est_size,
        commands=cmds,
        output_dir=out_dir,
        output_files=files,
        output_glob=f"{info.path.stem}_part*{plan.output_ext}",
        encode_plan=plan,
        warnings=plan.warnings,
        jobs=jobs,
    )


def plan_size(
    info: VideoInfo,
    target_mb: float,
    caps: Capabilities,
    opts: EncodeOptions,
    *,
    lossless: bool,
    safety: float = SIZE_SAFETY,
    outdir: str | Path | None = None,
    jobs: int = 1,
) -> SplitPlan:
    out_dir = _output_dir(info, outdir)
    target_bytes = int(target_mb * 1024 * 1024)
    if target_bytes <= 0:
        raise ValueError("目标大小必须大于 0")

    if lossless:
        bps = info.overall_bitrate_bps
        if bps <= 0:
            raise ValueError("无法估算源码率，无法按大小无损切分")
        seconds = target_bytes * 8 / bps * safety
        ext = _copy_ext(info)
        pattern = _pattern(info, out_dir, ext)
        est_parts = max(1, math.ceil(info.duration / seconds))
        est_size = int(bps * seconds / 8)
        return SplitPlan(
            mode="size-copy",
            segment_seconds=seconds,
            estimated_parts=est_parts,
            estimated_part_size=est_size,
            commands=[_copy_cmd(info, seconds, pattern)],
            output_dir=out_dir,
            output_files=None,
            output_glob=f"{info.path.stem}_part*{ext}",
            encode_plan=None,
            warnings=_copy_split_warnings(
                info, "无损按大小切分：切点受关键帧限制，单段大小会有波动（已留余量）。"
            ),
        )

    plan = build_plan(info, opts, caps, force_bitrate=True)
    total_kbps = plan.total_bitrate_kbps
    if not total_kbps:
        raise RuntimeError("无法确定目标码率")
    seconds = target_bytes * 8 / (total_kbps * 1000) * safety
    cmds, files = _encode_commands(info, plan, seconds, out_dir)
    est_size = int(total_kbps * 1000 * seconds / 8)
    return SplitPlan(
        mode="size-encode",
        segment_seconds=seconds,
        estimated_parts=len(files),
        estimated_part_size=est_size,
        commands=cmds,
        output_dir=out_dir,
        output_files=files,
        output_glob=f"{info.path.stem}_part*{plan.output_ext}",
        encode_plan=plan,
        warnings=plan.warnings,
        jobs=jobs,
    )


def _trim_out_path(info: VideoInfo, outdir: str | Path | None, ext: str) -> Path:
    if outdir:
        p = Path(outdir)
        # 视为目录（无扩展名）或明确的输出文件（有扩展名）。
        if p.suffix:
            return p
        return p / f"{info.path.stem}_clip{ext}"
    return info.path.parent / f"{info.path.stem}_clip{ext}"


def plan_trim(
    info: VideoInfo,
    start: float,
    end: float | None,
    caps: Capabilities,
    opts: EncodeOptions,
    *,
    transcode: bool,
    outdir: str | Path | None = None,
) -> SplitPlan:
    """裁剪出 [start, end) 的单个子片段。end 为 None 表示直到片尾。"""
    if start < 0:
        raise ValueError("--from 不能为负")
    if info.duration and start >= info.duration:
        raise ValueError(
            f"--from ({start:.3f}s) 超过视频时长 ({info.duration:.3f}s)"
        )
    stop = end if end is not None else info.duration
    if stop is not None and stop <= start:
        raise ValueError("--to 必须大于 --from")
    dur = (stop - start) if stop else max(0.0, info.duration - start)

    if not transcode:
        out = _trim_out_path(info, outdir, info.path.suffix or ".mp4")
        cmd = [
            runner.ffmpeg(), "-y", "-hide_banner",
            "-ss", f"{start:.3f}", "-i", str(info.path),
        ]
        if end is not None:
            cmd += ["-t", f"{dur:.3f}"]
        cmd += ["-map", "0", "-c", "copy", *runner.faststart_args(out), str(out)]
        est_size = int(info.overall_bitrate_bps * dur / 8)
        return SplitPlan(
            mode="trim-copy",
            segment_seconds=dur,
            estimated_parts=1,
            estimated_part_size=est_size,
            commands=[cmd],
            output_dir=out.parent,
            output_files=[out],
            output_glob=out.name,
            encode_plan=None,
            warnings=[
                "无损裁剪：切点落在最近的关键帧上，起点可能略有前移。"
            ],
        )

    plan = build_plan(info, opts, caps)
    out = _trim_out_path(info, outdir, plan.output_ext)
    cmd = _encode_seg_cmd(info, plan, start, dur, out)
    total_kbps = plan.total_bitrate_kbps or info.overall_bitrate_bps // 1000
    est_size = int(total_kbps * 1000 * dur / 8)
    return SplitPlan(
        mode="trim-encode",
        segment_seconds=dur,
        estimated_parts=1,
        estimated_part_size=est_size,
        commands=[cmd],
        output_dir=out.parent,
        output_files=[out],
        output_glob=out.name,
        encode_plan=plan,
        warnings=plan.warnings,
    )


def _encode_full_cmd(info: VideoInfo, plan: EncodePlan, outfile: Path) -> list[str]:
    """整片转码为单个文件（不切分）。"""
    cmd = [
        runner.ffmpeg(), "-y", "-hide_banner", "-i", str(info.path),
        "-map", "0:v:0", "-map", "0:a?",
    ]
    if plan.vf:
        cmd += ["-vf", plan.vf]
    cmd += plan.video_args + plan.audio_args
    cmd += [*runner.faststart_args(outfile), str(outfile)]
    return cmd


def _shrink_label(info: VideoInfo, opts: EncodeOptions) -> str:
    """输出文件名后缀标签：用**实际生效**高度（只缩小不放大），否则 small。"""
    _, th = parse_resolution(opts.resolution)
    if not th:
        return "small"
    eff = th if (not info.height or th < info.height) else info.height
    return f"{eff}p"


def _shrink_out_path(info: VideoInfo, outdir: str | Path | None, label: str) -> Path:
    if outdir:
        p = Path(outdir)
        if p.suffix:  # 明确的输出文件
            return p
        return p / f"{info.path.stem}_{label}.mp4"
    return info.path.parent / f"{info.path.stem}_{label}.mp4"


def plan_shrink(
    info: VideoInfo,
    caps: Capabilities,
    opts: EncodeOptions,
    *,
    target_mb: float | None = None,
    outdir: str | Path | None = None,
) -> SplitPlan:
    """把整段视频转码压缩为**单个**较小的文件（默认降到 720p，适合社交分享）。

    给了 target_mb 时按时长反推目标码率，尽量贴近目标大小；否则用质量模式
    （默认码率 / 或用户的 --crf / --bitrate）。
    """
    force_bitrate = False
    if target_mb is not None:
        if target_mb <= 0:
            raise ValueError("目标大小必须大于 0")
        if info.duration <= 0:
            raise ValueError("无法获取时长，无法按大小压缩")
        total_kbps = int(target_mb * 1024 * 1024 * 8 / info.duration / 1000 * SIZE_SAFETY)
        audio_kbps = (
            0 if opts.audio_copy or not info.has_audio else opts.audio_bitrate_kbps
        )
        video_kbps = max(100, total_kbps - audio_kbps)
        opts = replace(opts, bitrate_kbps=video_kbps, crf=None)
        force_bitrate = True

    plan = build_plan(info, opts, caps, force_bitrate=force_bitrate)
    label = _shrink_label(info, opts)
    out = _shrink_out_path(info, outdir, label)
    cmd = _encode_full_cmd(info, plan, out)
    total_kbps = plan.total_bitrate_kbps or info.overall_bitrate_bps // 1000
    est_size = int(total_kbps * 1000 * info.duration / 8) if info.duration else 0
    return SplitPlan(
        mode="shrink",
        segment_seconds=info.duration,
        estimated_parts=1,
        estimated_part_size=est_size,
        commands=[cmd],
        output_dir=out.parent,
        output_files=[out],
        output_glob=out.name,
        encode_plan=plan,
        warnings=plan.warnings,
    )
