import pytest

from vclip import runner


def test_env_override_takes_precedence(monkeypatch):
    runner.tool_path.cache_clear()
    monkeypatch.setenv("VCLIP_FFMPEG", "/custom/ffmpeg")
    assert runner.tool_path("ffmpeg") == "/custom/ffmpeg"
    runner.tool_path.cache_clear()


def test_uppercase_tool_env_override(monkeypatch):
    runner.tool_path.cache_clear()
    monkeypatch.delenv("VCLIP_FFPROBE", raising=False)
    monkeypatch.setenv("FFPROBE", "/opt/ffprobe")
    assert runner.tool_path("ffprobe") == "/opt/ffprobe"
    runner.tool_path.cache_clear()


def test_missing_tool_raises(monkeypatch):
    runner.tool_path.cache_clear()
    monkeypatch.delenv("VCLIP_FFMPEG", raising=False)
    monkeypatch.delenv("FFMPEG", raising=False)
    monkeypatch.setattr(runner.shutil, "which", lambda _: None)
    with pytest.raises(runner.FFmpegNotFound):
        runner.tool_path("ffmpeg")
    runner.tool_path.cache_clear()


def test_faststart_args_only_for_mp4_family():
    assert runner.faststart_args("out.mp4") == ["-movflags", "+faststart"]
    assert runner.faststart_args("out.mov") == ["-movflags", "+faststart"]
    assert runner.faststart_args("out.M4V") == ["-movflags", "+faststart"]
    # 非 mp4 系容器不应带该 flag
    assert runner.faststart_args("out.mkv") == []
    assert runner.faststart_args("out.webm") == []
    assert runner.faststart_args("out.ts") == []
