from vclip.capabilities import _names
from conftest import make_caps


def test_hw_encoder_prefers_videotoolbox():
    caps = make_caps(vt_h264=True, nvenc_h264=True)
    assert caps.hw_encoder("h264") == "h264_videotoolbox"


def test_hw_encoder_nvenc_when_no_apple():
    caps = make_caps(nvenc_hevc=True)
    assert caps.hw_encoder("hevc") == "hevc_nvenc"


def test_hw_encoder_qsv_then_amf_order():
    caps = make_caps(qsv_h264=True, amf_h264=True)
    assert caps.hw_encoder("h264") == "h264_qsv"
    caps2 = make_caps(amf_h264=True)
    assert caps2.hw_encoder("h264") == "h264_amf"


def test_hw_encoder_none_when_only_vaapi():
    # vaapi 需要显式设备，不参与自动选择
    caps = make_caps(vaapi_h264=True, vaapi_hevc=True)
    assert caps.hw_encoder("h264") is None
    assert caps.hw_encoder("hevc") is None


def test_has_hardware():
    assert make_caps().has_hardware is False
    assert make_caps(nvenc_h264=True).has_hardware is True


def test_can_tonemap_hdr():
    assert make_caps(zscale=True, tonemap=True).can_tonemap_hdr is True
    assert make_caps(zscale=False, tonemap=True, libplacebo=False).can_tonemap_hdr is False
    assert make_caps(zscale=False, tonemap=False, libplacebo=True).can_tonemap_hdr is True


def test_hw_encoder_unknown_codec_returns_none():
    assert make_caps(vt_h264=True).hw_encoder("vp9") is None


def test_names_exact_match_avoids_substring_false_positive():
    text = (
        "Encoders:\n"
        " V..... = Video\n"
        " ------\n"
        " V....D libx264rgb           libx264 rgb\n"
        " V....D hevc_nvenc           NVIDIA HEVC\n"
    )
    names = _names(text)
    assert "hevc_nvenc" in names
    assert "libx264rgb" in names
    # 只有 libx264rgb，不应误报出裸的 libx264
    assert "libx264" not in names


def test_names_parses_filter_output():
    text = (
        "Filters:\n"
        "  T.. = Timeline support\n"
        " ... tonemap           V->V       Conversion\n"
        " ... tonemap_opencl    V->V       OpenCL\n"
    )
    names = _names(text)
    assert "tonemap" in names
    assert "tonemap_opencl" in names


def test_caps_human_shows_auto_selection():
    caps = make_caps(vt_h264=True)
    h = caps.human()
    assert "本机 ffmpeg 能力" in h
    assert "h264_videotoolbox" in h  # 自动选用行


def test_caps_as_dict_serializable():
    import json
    caps = make_caps(vt_h264=True)
    d = caps.as_dict()
    assert d["auto_h264"] == "h264_videotoolbox"
    assert d["auto_hevc"] == "libx265"  # 无 hevc 硬件 → 软件回退
    assert d["can_tonemap_hdr"] is True
    json.dumps(d)
