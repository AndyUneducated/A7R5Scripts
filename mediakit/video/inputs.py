"""片段输入解析：把命令行参数（一个目录或多个文件）解析成有序的片段列表。

merge（重组）与 verify（校验）两个命令都需要"按顺序收集若干片段"，因此把这个
共享逻辑独立出来，避免 CLI 反向依赖 merge 的内部私有函数，也让边界更清晰。
"""
from __future__ import annotations

import re
from pathlib import Path

# 可识别为视频片段的扩展名（用于传入目录时自动收集）。
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi", ".ts", ".m2ts"}


def natural_key(path: Path) -> list:
    """自然排序键：让 part2 排在 part10 之前（而非字典序）。"""
    return [
        int(tok) if tok.isdigit() else tok.lower()
        for tok in re.split(r"(\d+)", path.name)
    ]


def resolve_inputs(inputs: list[str]) -> list[Path]:
    """把命令行参数解析成有序的片段文件列表。

    - 传入单个目录：自动收集其中的视频文件并按文件名自然排序。
    - 传入多个文件：保持给定顺序（顺序即拼接 / 校验顺序）。
    """
    if len(inputs) == 1 and Path(inputs[0]).is_dir():
        d = Path(inputs[0])
        files = sorted(
            (
                p for p in d.iterdir()
                if p.is_file()
                and p.suffix.lower() in VIDEO_EXTS
                # 排除本工具自己产出的重组文件，避免二次 merge 把结果又拼进去。
                and not p.stem.endswith("_merged")
            ),
            key=natural_key,
        )
        if not files:
            raise ValueError(f"目录 {d} 里没有可识别的视频片段")
        return files

    files = [Path(x) for x in inputs]
    for p in files:
        if not p.exists():
            raise ValueError(f"文件不存在：{p}")
        if not p.is_file():
            raise ValueError(f"不是文件：{p}")
    return files
