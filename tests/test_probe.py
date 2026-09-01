import json

from vclip.probe import _fraction, format_duration, format_size
from conftest import make_info, make_sdr_info


def test_fraction():
    assert _fraction("60000/1001") == 60000 / 1001
    assert _fraction("30") == 30.0
    assert _fraction("1/0") == 0.0
    assert _fraction("") == 0.0
    assert _fraction(None) == 0.0
    assert _fraction("bad") == 0.0


def test_format_duration():
    assert format_duration(75) == "1:15"
    assert format_duration(3661) == "1:01:01"
    assert format_duration(0) == "0:00"
    assert format_duration(-5) == "0:00"


def test_format_size():
    assert format_size(500) == "500 B"
    assert format_size(1536) == "1.5 KB"
    assert format_size(1024 * 1024 * 200) == "200.0 MB"


def test_hdr_detection():
    assert make_info().is_hdr is True
    assert make_sdr_info().is_hdr is False


def test_10bit_detection():
    assert make_info(pix_fmt="yuv420p10le").is_10bit is True
    assert make_info(pix_fmt="yuv420p").is_10bit is False


def test_overall_bitrate_uses_size_over_duration():
    info = make_info(size_bytes=120_000_000, duration=120.0)
    assert info.overall_bitrate_bps == int(120_000_000 * 8 / 120)


def test_overall_bitrate_falls_back_to_stream():
    info = make_info(duration=0.0, stream_bitrate=5000)
    assert info.overall_bitrate_bps == 5000


def test_as_dict_is_json_serializable():
    d = make_info().as_dict()
    assert d["is_hdr"] is True
    assert d["path"] == "/tmp/movie.mp4"
    assert "overall_bitrate_bps" in d
    assert d["audio_sample_rate"] == 48000
    assert d["audio_channels"] == 2
    assert d["sar"] == "1:1"
    json.dumps(d)  # 不应抛异常


def test_audio_human():
    assert make_info(has_audio=False, audio_codec=None,
                     audio_sample_rate=0, audio_channels=0)._audio_human() == "无"
    assert "48000 Hz" in make_info()._audio_human()
    assert "2ch" in make_info()._audio_human()
