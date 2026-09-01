"""Top-level CLI: `mediakit photo|video|time …`, plus a `vclip` compatibility entry."""

from __future__ import annotations

import argparse
import sys

from mediakit import __version__

from . import photo as photo_cli
from . import time as time_cli
from . import video as video_cli


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mediakit",
        description=(
            "照片压缩、视频切分/重组、拍摄时间修复。"
            " 常用：mediakit photo shrink  ·  mediakit video split  ·  mediakit video merge"
        ),
    )
    parser.add_argument("--version", action="version", version=f"mediakit {__version__}")
    sub = parser.add_subparsers(dest="domain", required=True)

    photo_cli.register(sub.add_parser("photo", help="批量压缩照片（HEIF / JPEG）"))
    video_cli.register(sub.add_parser("video", help="切分 / 压缩 / 无损合并视频"))
    time_cli.register(
        sub.add_parser("time", help="修复拍摄时间 metadata（不重新编码）")
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.domain == "photo":
        return photo_cli.run(args)
    if args.domain == "video":
        return video_cli.run(args)
    if args.domain == "time":
        return time_cli.run(args)
    parser.error(f"unknown domain {args.domain}")
    return 2


def vclip_main(argv: list[str] | None = None) -> int:
    """Compatibility entry: `vclip duration …` → `mediakit video duration …`."""
    rest = list(sys.argv[1:] if argv is None else argv)
    return main(["video", *rest])


if __name__ == "__main__":
    sys.exit(main())
