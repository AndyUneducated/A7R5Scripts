"""vclip 命令行入口。

子命令:
  info       查看视频信息（分辨率/码率/HDR 等）
  caps       查看本机 ffmpeg 能力（编码器/滤镜/硬件）
  shrink     整片压缩为单个小视频（默认 720p，适合社交分享）
  size       按目标大小切分（默认策略，默认每段 200MB；默认转码，可 --lossless 无损）
  duration   按时长切分（默认无损 -c copy，可 --transcode 转码）
  trim       裁剪出一个子片段（--from/--to，默认无损 -c copy）
  merge      无损重组：把切分后的片段拼回一个视频（严格 -c copy）
  verify     逐帧校验：整体文件 == 若干片段按序拼接（无损合并/切分通用）
  social     预设：切成多个 ≤30s、1080p、H.264 短片（社交平台）
  share      预设：切成多个 ≤指定大小的 1080p H.264 片段（分享 / 发文件）
"""
from __future__ import annotations

import argparse
import json
import sys
import time

from . import __version__
from .capabilities import detect
from .encode import EncodeOptions
from .inputs import resolve_inputs
from .merge import plan_merge
from .pipeline import ConsoleReporter, Plan
from .probe import ProbeError, format_size, probe
from .split import plan_duration, plan_shrink, plan_size, plan_trim
from .verify import verify_concat


def _add_encode_args(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("编码选项（转码时生效）")
    g.add_argument("--codec", choices=["h264", "hevc"], default="h264",
                   help="视频编码，默认 h264（兼容性最好）")
    g.add_argument("--encoder", choices=["auto", "hardware", "software"],
                   default="auto", help="编码器：auto 优先硬件(videotoolbox)，快")
    g.add_argument("--resolution", "-r", default=None,
                   help="目标分辨率：1080p/720p/4k 或 1920x1080，默认保持原分辨率")
    g.add_argument("--fps", type=float, default=None, help="目标帧率，默认保持")
    g.add_argument("--crf", type=int, default=None,
                   help="软件编码质量(18~28常用，越小越清晰)；指定后强制软件编码")
    g.add_argument("--bitrate", type=int, default=None,
                   help="目标视频码率 (kbps)")
    g.add_argument("--audio-bitrate", type=int, default=128,
                   help="音频码率 (kbps)，默认 128")
    g.add_argument("--audio-copy", action="store_true", help="直接复制音频流")
    g.add_argument("--hdr", choices=["auto", "sdr", "keep"], default="auto",
                   help="HDR 处理：auto/sdr(转SDR)/keep(保留HDR)")
    g.add_argument("--preset", default="medium",
                   help="x264/x265 的 -preset，默认 medium")


def _add_execution_flags(p: argparse.ArgumentParser) -> None:
    """所有会执行 ffmpeg 的子命令共享的执行控制开关。"""
    p.add_argument("--dry-run", action="store_true", help="只打印将执行的命令，不实际运行")
    p.add_argument("--yes", "-y", action="store_true", help="跳过确认直接执行")


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("file", help="输入视频文件")
    p.add_argument("--outdir", "-o", default=None, help="输出目录")
    p.add_argument("--jobs", "-j", type=int, default=1,
                   help="转码时并行编码的段数（默认 1，串行）")
    p.add_argument("--verify", action="store_true",
                   help="执行后逐帧校验无损（仅对无损切分有意义，需完整解码）")
    _add_execution_flags(p)


def _opts_from_args(a: argparse.Namespace) -> EncodeOptions:
    return EncodeOptions(
        codec=a.codec,
        encoder=a.encoder,
        resolution=a.resolution,
        fps=a.fps,
        crf=a.crf,
        bitrate_kbps=a.bitrate,
        audio_bitrate_kbps=a.audio_bitrate,
        audio_copy=a.audio_copy,
        hdr=a.hdr,
        x26x_preset=a.preset,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vclip",
        description="通用视频切分 / 重组工具：把长视频切成多个小片段，或把片段无损拼回一个视频",
    )
    parser.add_argument("--version", action="version", version=f"vclip {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_info = sub.add_parser("info", help="查看视频信息")
    p_info.add_argument("file", help="输入视频文件")
    p_info.add_argument("--json", action="store_true",
                        help="以 JSON 输出（便于脚本 / 管道处理）")

    p_caps = sub.add_parser("caps", help="查看本机 ffmpeg 能力（编码器/滤镜/硬件）")
    p_caps.add_argument("--json", action="store_true", help="以 JSON 输出")

    p_size = sub.add_parser("size", help="按目标大小切分（默认策略，默认 200MB）")
    _add_common(p_size)
    p_size.add_argument("--target-mb", "-m", type=float, default=200,
                        help="每段目标大小 (MB)，默认 200")
    p_size.add_argument("--lossless", action="store_true",
                        help="无损按大小切分（-c copy，保留原画质/HDR）")
    _add_encode_args(p_size)

    p_dur = sub.add_parser("duration", help="按时长切分")
    _add_common(p_dur)
    p_dur.add_argument("--seconds", "-s", type=float, required=True,
                       help="每段时长（秒）")
    p_dur.add_argument("--transcode", action="store_true",
                       help="转码切分（默认无损 -c copy）")
    _add_encode_args(p_dur)

    p_shrink = sub.add_parser(
        "shrink", help="整片压缩为单个小视频（默认 720p，适合社交分享）")
    p_shrink.add_argument("file", help="输入视频文件")
    p_shrink.add_argument("--output", "-o", default=None,
                          help="输出文件或目录（默认 <名字>_720p.mp4）")
    p_shrink.add_argument("--target-mb", "-m", type=float, default=None,
                          help="目标文件大小 (MB)：按时长反推码率尽量贴近")
    _add_execution_flags(p_shrink)
    _add_encode_args(p_shrink)

    p_trim = sub.add_parser("trim", help="裁剪出一个子片段（默认无损 -c copy）")
    p_trim.add_argument("file", help="输入视频文件")
    p_trim.add_argument("--from", dest="start", type=float, required=True,
                        help="起点（秒）")
    p_trim.add_argument("--to", dest="end", type=float, default=None,
                        help="终点（秒），省略则到片尾")
    p_trim.add_argument("--output", "-o", default=None,
                        help="输出文件或目录（默认 <名字>_clip.<扩展名>）")
    p_trim.add_argument("--transcode", action="store_true",
                        help="转码裁剪（默认无损 -c copy）")
    _add_execution_flags(p_trim)
    _add_encode_args(p_trim)

    p_merge = sub.add_parser("merge", help="无损重组：把片段拼回一个视频（严格 -c copy）")
    p_merge.add_argument("inputs", nargs="+",
                         help="片段文件（按顺序），或一个包含片段的目录")
    p_merge.add_argument("--output", "-o", default=None,
                         help="输出文件（默认 <名字>_merged.<原扩展名>）")
    p_merge.add_argument("--verify", action="store_true",
                         help="合并后逐帧校验无损（需完整解码，较慢）")
    _add_execution_flags(p_merge)

    p_verify = sub.add_parser(
        "verify", help="逐帧校验：整体文件 == 若干片段按序拼接（无损合并/切分通用）")
    p_verify.add_argument("whole", help="整体文件（合并输出，或切分前的源）")
    p_verify.add_argument("parts", nargs="+",
                          help="片段文件（按顺序），或一个包含片段的目录")

    p_social = sub.add_parser("social", help="预设：社交短片 ≤30s 1080p H.264")
    _add_common(p_social)
    p_social.add_argument("--seconds", "-s", type=float, default=30,
                          help="每段时长，默认 30 秒")
    _add_encode_args(p_social)

    p_share = sub.add_parser("share", help="预设：分享/发文件，按大小切 1080p H.264")
    _add_common(p_share)
    p_share.add_argument("--target-mb", "-m", type=float, default=200,
                         help="每段目标大小 (MB)，默认 200")
    _add_encode_args(p_share)

    return parser


def _run_verify(whole, parts) -> int:
    """逐帧无损校验，打印结果并返回退出码。"""
    print("\n正在逐帧校验无损（完整解码，可能较慢）…")
    start = time.monotonic()
    report = verify_concat(whole, parts)
    print(report.human())
    print(f"  （校验耗时 {time.monotonic() - start:.1f}s）")
    return 0 if report.ok else 1


def _confirm_and_run(plan: Plan, *, dry_run: bool, yes: bool, verify_fn=None) -> int:
    print(plan.describe())
    for w in plan.warnings:
        print(f"  ⚠️  {w}")

    if not dry_run and not yes:
        try:
            ans = input("\n开始执行？[Y/n] ").strip().lower()
        except EOFError:
            ans = "y"
        if ans in {"n", "no"}:
            print("已取消。")
            return 1

    start = time.monotonic()
    files = plan.execute(dry_run=dry_run, reporter=ConsoleReporter())
    if dry_run:
        print("（dry-run，未实际执行）")
        return 0

    elapsed = time.monotonic() - start
    print(f"\n✅ 完成，共 {len(files)} 个文件（耗时 {elapsed:.1f}s）：")
    total = 0
    for f in files:
        sz = f.stat().st_size
        total += sz
        print(f"  {f.name}  ({format_size(sz)})")
    print(f"  合计 {format_size(total)}")

    if verify_fn is not None:
        return verify_fn(files)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        if args.command == "info":
            info = probe(args.file)
            if args.json:
                print(json.dumps(info.as_dict(), ensure_ascii=False, indent=2))
            else:
                print(info.human())
            return 0

        if args.command == "caps":
            caps = detect()
            if args.json:
                print(json.dumps(caps.as_dict(), ensure_ascii=False, indent=2))
            else:
                print(caps.human())
            return 0

        if args.command == "verify":
            parts = resolve_inputs(args.parts)
            return _run_verify(args.whole, parts)

        if args.command == "merge":
            plan = plan_merge(args.inputs, args.output)
            verify_fn = None
            if args.verify:
                parts = [i.path for i in plan.inputs]
                verify_fn = lambda _files: _run_verify(plan.output, parts)
            return _confirm_and_run(
                plan, dry_run=args.dry_run, yes=args.yes, verify_fn=verify_fn)

        info = probe(args.file)
        caps = detect()

        if args.command == "shrink":
            if args.resolution is None:
                args.resolution = "720p"      # 社交分享默认降到 720p
            if args.hdr == "auto":
                args.hdr = "sdr"              # 兼容性优先
            opts = _opts_from_args(args)
            plan = plan_shrink(
                info, caps, opts, target_mb=args.target_mb, outdir=args.output,
            )
            return _confirm_and_run(plan, dry_run=args.dry_run, yes=args.yes)

        if args.command == "trim":
            opts = _opts_from_args(args)
            plan = plan_trim(
                info, args.start, args.end, caps, opts,
                transcode=args.transcode, outdir=args.output,
            )
            return _confirm_and_run(plan, dry_run=args.dry_run, yes=args.yes)

        if args.command == "size":
            opts = _opts_from_args(args)
            plan = plan_size(
                info, args.target_mb, caps, opts,
                lossless=args.lossless, outdir=args.outdir, jobs=args.jobs,
            )
        elif args.command == "duration":
            opts = _opts_from_args(args)
            plan = plan_duration(
                info, args.seconds, caps, opts,
                transcode=args.transcode, outdir=args.outdir, jobs=args.jobs,
            )
        elif args.command == "social":
            if args.resolution is None:
                args.resolution = "1080p"
            if args.hdr == "auto":
                args.hdr = "sdr"  # 社交平台预设：优先兼容性
            opts = _opts_from_args(args)
            plan = plan_duration(
                info, args.seconds, caps, opts,
                transcode=True, outdir=args.outdir, jobs=args.jobs,
            )
        elif args.command == "share":
            if args.resolution is None:
                args.resolution = "1080p"
            if args.hdr == "auto":
                args.hdr = "sdr"  # 分享预设：优先兼容性
            opts = _opts_from_args(args)
            plan = plan_size(
                info, args.target_mb, caps, opts,
                lossless=False, outdir=args.outdir, jobs=args.jobs,
            )
        else:  # pragma: no cover
            print(f"未知命令：{args.command}", file=sys.stderr)
            return 2

        verify_fn = None
        if getattr(args, "verify", False):
            if plan.encode_plan is None:  # 无损（-c copy）才有逐帧无损可言
                verify_fn = lambda files: _run_verify(info.path, files)
            else:
                print("  ⚠️  --verify 仅对无损切分有意义，转码模式已跳过校验。")
        return _confirm_and_run(
            plan, dry_run=args.dry_run, yes=args.yes, verify_fn=verify_fn)

    except (ProbeError, ValueError, RuntimeError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:  # pragma: no cover
        print("\n已中断。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
