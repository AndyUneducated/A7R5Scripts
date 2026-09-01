import pytest

from mediakit.cli import build_parser as root_parser
from mediakit.cli import vclip_main
from mediakit.cli.video import build_parser, main


def test_parser_requires_subcommand():
    with pytest.raises(SystemExit):
        build_parser().parse_args([])


def test_duration_requires_seconds():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["duration", "x.mp4"])


def test_trim_parses_from_to():
    args = build_parser().parse_args(["trim", "x.mp4", "--from", "5", "--to", "20"])
    assert args.command == "trim"
    assert args.start == 5.0
    assert args.end == 20.0


def test_jobs_default_and_override():
    a = build_parser().parse_args(["duration", "x.mp4", "-s", "10"])
    assert a.jobs == 1
    b = build_parser().parse_args(["duration", "x.mp4", "-s", "10", "-j", "4"])
    assert b.jobs == 4


def test_info_json_flag():
    a = build_parser().parse_args(["info", "x.mp4", "--json"])
    assert a.json is True


def test_caps_subcommand_parses():
    a = build_parser().parse_args(["caps", "--json"])
    assert a.command == "caps"
    assert a.json is True


def test_verify_subcommand_parses():
    a = build_parser().parse_args(["verify", "whole.mp4", "p1.mp4", "p2.mp4"])
    assert a.command == "verify"
    assert a.whole == "whole.mp4"
    assert a.parts == ["p1.mp4", "p2.mp4"]


def test_verify_flag_on_merge_and_split():
    a = build_parser().parse_args(["merge", "p1.mp4", "p2.mp4", "--verify"])
    assert a.verify is True
    b = build_parser().parse_args(["duration", "x.mp4", "-s", "10", "--verify"])
    assert b.verify is True


def test_main_missing_file_returns_1():
    assert main(["info", "/no/such/file_xyz.mp4"]) == 1


def test_main_merge_too_few_returns_1(tmp_path):
    f = tmp_path / "only.mp4"
    f.write_bytes(b"x")
    assert main(["merge", str(f)]) == 1


def test_split_duration_parses():
    args = build_parser().parse_args(["split", "x.mp4", "--duration", "60"])
    assert args.command == "split"
    assert args.seconds == 60.0
    assert args.target_mb is None


def test_split_size_parses():
    args = build_parser().parse_args(["split", "x.mp4", "--size", "200"])
    assert args.command == "split"
    assert args.target_mb == 200.0


def test_split_profile_parses():
    args = build_parser().parse_args(["split", "x.mp4", "--profile", "social"])
    assert args.profile == "social"


def test_root_parser_nests_video():
    args = root_parser().parse_args(["video", "duration", "x.mp4", "-s", "10"])
    assert args.domain == "video"
    assert args.command == "duration"
    assert args.seconds == 10.0


def test_root_parser_nests_photo():
    args = root_parser().parse_args(["photo", "shrink", "in", "out"])
    assert args.domain == "photo"
    assert args.photo_command == "shrink"


def test_vclip_main_forwards_to_video():
    assert vclip_main(["info", "/no/such/file_xyz.mp4"]) == 1
