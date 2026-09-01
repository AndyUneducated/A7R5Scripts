import vclip.verify as V
from vclip.verify import StreamCheck, VerifyReport, compare_sequences, verify_concat


def test_compare_identical():
    ok, first = compare_sequences(["a", "b", "c"], ["a", "b", "c"])
    assert ok is True
    assert first is None


def test_compare_length_mismatch_prefix_matches():
    ok, first = compare_sequences(["a", "b", "c"], ["a", "b"])
    assert ok is False
    assert first is None  # 前缀一致，仅长度不同


def test_compare_pixel_mismatch_index():
    ok, first = compare_sequences(["a", "b", "c", "d"], ["a", "b", "X", "d"])
    assert ok is False
    assert first == 2


def test_compare_empty():
    ok, first = compare_sequences([], [])
    assert ok is True
    assert first is None


def _video_check(ok, expected, actual, first=None):
    return StreamCheck("视频", "逐帧像素", ok, expected, actual, first)


def test_report_ok_when_all_checks_ok():
    r = VerifyReport(checks=[
        _video_check(True, 600, 600),
        StreamCheck("音频#0", "包计数", True, 518, 518),
    ])
    assert r.ok is True
    assert "通过" in r.human()
    assert "600 帧" in r.human()


def test_report_fails_if_any_check_fails():
    r = VerifyReport(
        checks=[
            _video_check(False, 600, 594, first=237),
            StreamCheck("音频#0", "包计数", True, 518, 518),
        ],
        part_frame_counts=[237, 189, 168],
    )
    assert r.ok is False
    h = r.human()
    assert "未通过" in h
    assert "594" in h and "237" in h
    assert "--transcode" in h


def test_report_audio_packet_mismatch_fails():
    r = VerifyReport(checks=[
        _video_check(True, 600, 600),
        StreamCheck("音频#0", "包计数", False, 518, 500),
    ])
    assert r.ok is False
    assert "包数不一致" in r.human()


def test_report_empty_checks_not_ok():
    assert VerifyReport(checks=[]).ok is False


def test_verify_concat_all_ok(monkeypatch):
    # 两段各 2 帧 → 整体 4 帧；单音轨、包数相加一致 → 通过
    frames = {"w": ["a", "b", "c", "d"], "p1": ["a", "b"], "p2": ["c", "d"]}
    packets = {"w": 10, "p1": 5, "p2": 5}
    monkeypatch.setattr(V, "video_frame_hashes", lambda p: frames[str(p)])
    monkeypatch.setattr(V, "audio_track_count", lambda p: 1)
    monkeypatch.setattr(V, "audio_packet_count", lambda p, i: packets[str(p)])

    report = verify_concat("w", ["p1", "p2"])
    assert report.ok is True
    assert report.part_frame_counts == [2, 2]


def test_verify_concat_detects_frame_loss(monkeypatch):
    # 整体比片段拼接少 1 帧（模拟 open-GOP 边界丢帧）→ 不通过
    frames = {"w": ["a", "b", "c"], "p1": ["a", "b"], "p2": ["c", "d"]}
    monkeypatch.setattr(V, "video_frame_hashes", lambda p: frames[str(p)])
    monkeypatch.setattr(V, "audio_track_count", lambda p: 0)

    report = verify_concat("w", ["p1", "p2"])
    assert report.ok is False


def test_verify_concat_flags_audio_track_count_mismatch(monkeypatch):
    frames = {"w": ["a", "b"], "p1": ["a"], "p2": ["b"]}
    monkeypatch.setattr(V, "video_frame_hashes", lambda p: frames[str(p)])
    # 整体 2 条音轨，但某片段只有 1 条 → 轨道数不一致
    monkeypatch.setattr(
        V, "audio_track_count", lambda p: 2 if str(p) == "w" else 1
    )
    report = verify_concat("w", ["p1", "p2"])
    assert report.ok is False
    assert any(c.method == "轨道计数" for c in report.checks)
