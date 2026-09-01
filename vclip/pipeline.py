"""执行管线的共享契约：Plan（可描述 / 可执行的任务）与 Reporter（进度上报）。

把"执行"与"表现（打印）"分离：Plan.execute 只负责跑 ffmpeg 并返回产物，
过程中的进度通过 Reporter 上报，由调用方（CLI）决定如何呈现。这样领域层
（split / merge）不再直接依赖 stdout，执行路径也更容易在测试里静默运行。
"""
from __future__ import annotations

import shlex
from pathlib import Path
from typing import Protocol


class Reporter(Protocol):
    """执行过程中的进度上报接口。实现方决定如何呈现（打印 / 静默 / 收集）。"""

    def command(self, cmd: list[str], index: int = 1, total: int = 1) -> None:
        """即将执行的一条 ffmpeg 命令（index/total 用于多段场景）。"""

    def segment_start(self, index: int, total: int) -> None:
        """串行执行时，第 index/total 段开始。"""

    def segment_done(self, index: int, total: int) -> None:
        """第 index/total 段完成。"""

    def note(self, message: str) -> None:
        """一条自由文本提示（如并行编码开始）。"""


class NullReporter:
    """静默 Reporter：库内调用 / 单元测试的默认，什么也不打印。"""

    def command(self, cmd: list[str], index: int = 1, total: int = 1) -> None:
        pass

    def segment_start(self, index: int, total: int) -> None:
        pass

    def segment_done(self, index: int, total: int) -> None:
        pass

    def note(self, message: str) -> None:
        pass


class ConsoleReporter:
    """把进度打印到 stdout（CLI 默认）。"""

    def command(self, cmd: list[str], index: int = 1, total: int = 1) -> None:
        prefix = f"[{index}/{total}] " if total > 1 else ""
        print(f"\n{prefix}$ " + " ".join(shlex.quote(c) for c in cmd))

    def segment_start(self, index: int, total: int) -> None:
        if total > 1:
            print(f"\n── 第 {index}/{total} 段 ──")

    def segment_done(self, index: int, total: int) -> None:
        print(f"  ✓ 第 {index}/{total} 段完成")

    def note(self, message: str) -> None:
        print(message)


class Plan(Protocol):
    """一次可描述、可执行的任务（切分 / 重组等）的统一契约。

    CLI 只依赖这个协议，不关心具体是 SplitPlan 还是 MergePlan；两者以结构化
    子类型（duck typing）方式满足它，静态检查器可在传参处校验一致性。
    """

    warnings: list[str]

    def describe(self) -> str:
        """人类可读的任务摘要（执行前打印确认）。"""

    def execute(
        self, *, dry_run: bool = False, reporter: Reporter | None = None
    ) -> list[Path]:
        """执行任务，返回生成的文件列表。dry_run 时只上报命令、不实际运行。"""
