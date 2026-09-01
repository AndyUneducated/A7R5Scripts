import pytest

from mediakit.video import runner


def _clear_ffmpeg_env(monkeypatch) -> None:
    for name in (
        "MEDIAKIT_FFMPEG",
        "MEDIAKIT_FFPROBE",
        "VCLIP_FFMPEG",
        "VCLIP_FFPROBE",
        "FFMPEG",
        "FFPROBE",
    ):
        monkeypatch.delenv(name, raising=False)


def test_mediakit_env_override_takes_precedence(monkeypatch):
    runner.tool_path.cache_clear()
    _clear_ffmpeg_env(monkeypatch)
    monkeypatch.setenv("MEDIAKIT_FFMPEG", "/custom/ffmpeg")
    monkeypatch.setenv("VCLIP_FFMPEG", "/old/ffmpeg")
    assert runner.tool_path("ffmpeg") == "/custom/ffmpeg"
    runner.tool_path.cache_clear()


def test_vclip_env_override_still_works(monkeypatch):
    runner.tool_path.cache_clear()
    _clear_ffmpeg_env(monkeypatch)
    monkeypatch.setenv("VCLIP_FFMPEG", "/custom/ffmpeg")
    assert runner.tool_path("ffmpeg") == "/custom/ffmpeg"
    runner.tool_path.cache_clear()


def test_uppercase_tool_env_override(monkeypatch):
    runner.tool_path.cache_clear()
    _clear_ffmpeg_env(monkeypatch)
    monkeypatch.setenv("FFPROBE", "/opt/ffprobe")
    assert runner.tool_path("ffprobe") == "/opt/ffprobe"
    runner.tool_path.cache_clear()


def test_missing_tool_raises(monkeypatch):
    runner.tool_path.cache_clear()
    _clear_ffmpeg_env(monkeypatch)
    monkeypatch.setattr(runner.shutil, "which", lambda _: None)
    with pytest.raises(runner.FFmpegNotFound):
        runner.tool_path("ffmpeg")
    runner.tool_path.cache_clear()


def test_faststart_args_only_for_mp4_family():
    assert runner.faststart_args("out.mp4") == ["-movflags", "+faststart"]
    assert runner.faststart_args("out.mov") == ["-movflags", "+faststart"]
    assert runner.faststart_args("out.M4V") == ["-movflags", "+faststart"]
    assert runner.faststart_args("out.mkv") == []
    assert runner.faststart_args("out.webm") == []
    assert runner.faststart_args("out.ts") == []
