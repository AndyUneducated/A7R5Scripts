"""统一的 ffmpeg / ffprobe 定位与调用。

以前 ffmpeg/ffprobe 的查找散落在 probe / capabilities / split / merge 四处，
各写一份 `shutil.which` + 报错。这里集中一处，方便统一错误信息、测试打桩，
以及未来替换二进制路径（如通过环境变量指定）。
"""
from __future__ import annotations

import functools
import os
import shutil
import subprocess
from pathlib import Path

# `-movflags +faststart` 只对 mp4/mov 系容器有意义（把 moov 前置便于边下边播）。
_FASTSTART_EXTS = {".mp4", ".mov", ".m4v", ".m4a"}


class FFmpegNotFound(RuntimeError):
    """找不到 ffmpeg / ffprobe 可执行文件。"""


def faststart_args(output: str | Path) -> list[str]:
    """按输出容器决定是否加 `-movflags +faststart`。

    该 flag 是 mp4/mov 复用器私有选项；用在 mkv/webm/ts 等容器上虽被 ffmpeg
    静默忽略（不报错），但传一个用不上的参数并不干净。这里按扩展名精确判断。
    """
    return ["-movflags", "+faststart"] if Path(output).suffix.lower() in _FASTSTART_EXTS else []


@functools.lru_cache(maxsize=None)
def tool_path(tool: str) -> str:
    """定位 ffmpeg / ffprobe。

    支持用环境变量覆盖（如 CI / 非标准安装）：
      VCLIP_FFMPEG / VCLIP_FFPROBE，或大写工具名 FFMPEG / FFPROBE。
    """
    override = os.environ.get(f"VCLIP_{tool.upper()}") or os.environ.get(tool.upper())
    if override:
        return override
    path = shutil.which(tool)
    if not path:
        raise FFmpegNotFound(f"找不到 `{tool}`，请先安装 ffmpeg：brew install ffmpeg")
    return path


def ffmpeg() -> str:
    return tool_path("ffmpeg")


def ffprobe() -> str:
    return tool_path("ffprobe")


def run(cmd: list[str], *, capture: bool = False, check: bool = False):
    """执行一条命令。capture=True 时捕获 stdout/stderr（text 模式）。"""
    return subprocess.run(cmd, capture_output=capture, text=capture, check=check)
