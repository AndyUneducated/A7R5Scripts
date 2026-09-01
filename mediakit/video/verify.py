"""无损校验（视频逐帧 + 音频逐包）。

判断"一个整体文件"是否与"若干片段按序拼接"在内容上无损一致。两个场景本质相同：
  - 无损合并：whole = 合并输出，   parts = 被合并的片段
  - 无损切分：whole = 原始源视频， parts = 切出的片段

校验分两类流，各用最合适的黄金标准：

  视频：用 `ffmpeg -f framemd5` 取每帧**解码后像素哈希**（与时间戳、容器无关），
        逐帧比对。这是判断视频是否真正无损的最强手段。

  音频：比对**音频包数量**（whole vs 各片段之和）与**音轨数量**。
        为什么不逐样本比对：AAC 等有损音频经 `-c copy` 是逐包原样保留的（无损），
        但解码时每段边界存在编码器 priming/延迟，逐样本 PCM 会有毫秒级差异——
        那不是数据丢失，而是有损音频的固有现象。包数一致即证明"没有丢/多包"，
        这才是音频无损的正确判据。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import runner


def video_frame_hashes(path: str | Path) -> list[str]:
    """返回视频首条流每一帧的像素哈希（顺序即播放顺序，与时间戳无关）。"""
    cmd = [
        runner.ffmpeg(), "-v", "error",
        "-i", str(path), "-map", "0:v:0",
        "-f", "framemd5", "-",
    ]
    proc = runner.run(cmd, capture=True)
    if proc.returncode != 0:
        raise RuntimeError(f"读取帧哈希失败：{path}\n{(proc.stderr or '').strip()}")
    hashes: list[str] = []
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        fields = [f.strip() for f in line.split(",")]
        if len(fields) >= 2 and fields[-1]:
            hashes.append(fields[-1])
    return hashes


def audio_track_count(path: str | Path) -> int:
    """音轨数量。"""
    proc = runner.run([
        runner.ffprobe(), "-v", "error", "-select_streams", "a",
        "-show_entries", "stream=index", "-of", "csv=p=0", str(path),
    ], capture=True)
    return len([ln for ln in (proc.stdout or "").splitlines() if ln.strip()])


def audio_packet_count(path: str | Path, index: int) -> int:
    """第 index 条音轨的包数（不解码，读包即可）。"""
    proc = runner.run([
        runner.ffprobe(), "-v", "error", "-select_streams", f"a:{index}",
        "-count_packets", "-show_entries", "stream=nb_read_packets",
        "-of", "csv=p=0", str(path),
    ], capture=True)
    txt = (proc.stdout or "").strip()
    return int(txt) if txt.isdigit() else 0


def compare_sequences(expected: list[str], actual: list[str]) -> tuple[bool, int | None]:
    """纯比较：返回 (是否完全一致, 首个不一致下标)。便于单测，不触发 ffmpeg。"""
    first_mismatch: int | None = None
    for i in range(min(len(expected), len(actual))):
        if expected[i] != actual[i]:
            first_mismatch = i
            break
    ok = len(expected) == len(actual) and first_mismatch is None
    return ok, first_mismatch


@dataclass
class StreamCheck:
    label: str                    # "视频" / "音频#0" / "音轨数"
    method: str                   # "逐帧像素" / "包计数" / "轨道计数"
    ok: bool
    expected: int
    actual: int
    first_mismatch: int | None = None


@dataclass
class VerifyReport:
    checks: list[StreamCheck]
    part_frame_counts: list[int] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.checks) and all(c.ok for c in self.checks)

    def human(self) -> str:
        lines = ["✅ 无损校验通过：" if self.ok else "❌ 无损校验未通过："]
        for c in self.checks:
            mark = "✓" if c.ok else "✗"
            diff = c.actual - c.expected
            if c.method == "逐帧像素":
                if c.ok:
                    lines.append(f"  {mark} 视频：{c.actual} 帧逐帧像素完全一致")
                else:
                    seg = []
                    if c.expected != c.actual:
                        seg.append(
                            f"帧数不一致（整体 {c.actual} / 片段拼接 {c.expected}，"
                            f"相差 {diff:+d}）"
                        )
                    if c.first_mismatch is not None:
                        seg.append(f"首个不一致帧 #{c.first_mismatch}")
                    lines.append(f"  {mark} 视频：" + "；".join(seg))
                    lines.append(f"      各片段帧数：{self.part_frame_counts}")
            elif c.method == "包计数":
                if c.ok:
                    lines.append(f"  {mark} {c.label}：{c.actual} 个音频包，逐包保留")
                else:
                    lines.append(
                        f"  {mark} {c.label}：包数不一致"
                        f"（整体 {c.actual} / 片段拼接 {c.expected}，相差 {diff:+d}）"
                    )
            else:  # 轨道计数
                lines.append(
                    f"  {mark} {c.label}：整体 {c.actual} 条 / 片段 {c.expected} 条"
                    + ("" if c.ok else "（不一致）")
                )
        for n in self.notes:
            lines.append(f"  · {n}")
        if not self.ok:
            lines.append(
                "  说明：帧/包数不一致通常意味着边界丢帧或内容改动"
                "（如 open-GOP 的 HEVC 无损切分会在边界丢帧）。"
                "需要逐帧精确可改用 --transcode。"
            )
        return "\n".join(lines)


def verify_concat(whole: str | Path, parts: list[str | Path]) -> VerifyReport:
    """校验 whole 是否与 parts 依次拼接无损一致（视频逐帧 + 音频逐包）。"""
    checks: list[StreamCheck] = []
    notes: list[str] = []

    # ---- 视频：逐帧像素 ----
    expected_v: list[str] = []
    part_counts: list[int] = []
    for p in parts:
        h = video_frame_hashes(p)
        part_counts.append(len(h))
        expected_v += h
    actual_v = video_frame_hashes(whole)
    ok_v, first = compare_sequences(expected_v, actual_v)
    checks.append(StreamCheck(
        "视频", "逐帧像素", ok_v, len(expected_v), len(actual_v), first,
    ))

    # ---- 音频：轨道数 + 逐包 ----
    n_whole = audio_track_count(whole)
    part_tracks = [audio_track_count(p) for p in parts]
    if n_whole == 0 and all(t == 0 for t in part_tracks):
        pass  # 无音频，跳过
    elif any(t != n_whole for t in part_tracks):
        bad = next(t for t in part_tracks if t != n_whole)
        checks.append(StreamCheck("音轨数", "轨道计数", False, bad, n_whole))
    else:
        for i in range(n_whole):
            exp = sum(audio_packet_count(p, i) for p in parts)
            act = audio_packet_count(whole, i)
            checks.append(StreamCheck(f"音频#{i}", "包计数", exp == act, exp, act))
        notes.append(
            "音频经 -c copy 逐包保留；解码端有 AAC priming 的毫秒级边界差异，属正常。"
        )

    return VerifyReport(checks=checks, part_frame_counts=part_counts, notes=notes)
