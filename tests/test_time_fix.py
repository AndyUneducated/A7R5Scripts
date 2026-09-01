import pathlib
import tempfile
import unittest

from mediakit.cli import main as cli_main
from mediakit.time import fix as fpt


class ArgBuildingTest(unittest.TestCase):
    def test_image_shift_tags(self):
        args = fpt.shift_args(fpt.IMAGE_KIND, "+=0:0:0 1:00:00", "-07:00")
        self.assertIn("-AllDates+=0:0:0 1:00:00", args)
        self.assertIn("-XMP:DateCreated+=0:0:0 1:00:00", args)
        self.assertIn("-IFD1:ModifyDate+=0:0:0 1:00:00", args)
        self.assertIn("-OffsetTimeOriginal=-07:00", args)

    def test_video_gets_track_and_media_dates(self):
        args = fpt.shift_args(fpt.VIDEO_KIND, "+=0:0:0 1:00:00", "-07:00")
        for tag in (
            "TrackCreateDate",
            "TrackModifyDate",
            "MediaCreateDate",
            "MediaModifyDate",
        ):
            self.assertIn(f"-QuickTime:{tag}+=0:0:0 1:00:00", args)
        # EXIF offset tags do not exist in QuickTime containers.
        self.assertNotIn("-OffsetTimeOriginal=-07:00", args)

    def test_offset_only_run_has_no_date_shift(self):
        args = fpt.shift_args(fpt.IMAGE_KIND, "", "-07:00")
        self.assertEqual(
            args,
            [
                "-OffsetTime=-07:00",
                "-OffsetTimeOriginal=-07:00",
                "-OffsetTimeDigitized=-07:00",
            ],
        )

    def test_offset_only_run_skips_videos(self):
        self.assertEqual(fpt.shift_args(fpt.VIDEO_KIND, "", "-07:00"), [])

    def test_file_times_come_from_metadata(self):
        self.assertEqual(
            fpt.file_time_args(fpt.IMAGE_KIND),
            ["-FileModifyDate<DateTimeOriginal", "-FileCreateDate<DateTimeOriginal"],
        )
        self.assertEqual(
            fpt.file_time_args(fpt.VIDEO_KIND),
            [
                "-FileModifyDate<QuickTime:CreateDate",
                "-FileCreateDate<QuickTime:CreateDate",
            ],
        )

    def test_command_includes_quicktime_utc_and_files(self):
        cmd = fpt.build_command(["-AllDates+=0:0:0 1:00:00"], [pathlib.Path("a.arw")])
        self.assertEqual(cmd[0], "exiftool")
        self.assertIn("-overwrite_original", cmd)
        self.assertIn("QuickTimeUTC=1", cmd)
        self.assertEqual(cmd[-1], "a.arw")


class KindTest(unittest.TestCase):
    def test_kind_detection(self):
        self.assertEqual(fpt.kind_of(pathlib.Path("a.MP4")), fpt.VIDEO_KIND)
        self.assertEqual(fpt.kind_of(pathlib.Path("a.ARW")), fpt.IMAGE_KIND)


class ChunkTest(unittest.TestCase):
    def test_chunking(self):
        files = [pathlib.Path(f"{i}.arw") for i in range(5)]
        self.assertEqual([len(c) for c in fpt.chunk(files, 2)], [2, 2, 1])
        self.assertEqual(fpt.chunk([], 2), [])


class CopyAllTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def test_structure_is_preserved(self):
        src = self.root / "in" / "day1" / "a.arw"
        src.parent.mkdir(parents=True)
        src.write_bytes(b"raw")

        copied, errors = fpt.copy_all(
            [src], self.root / "in", self.root / "out", workers=2
        )

        self.assertEqual(errors, [])
        self.assertEqual(copied, [self.root / "out" / "day1" / "a.arw"])
        self.assertEqual(copied[0].read_bytes(), b"raw")


class MainValidationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        (self.root / "in").mkdir()
        self.addCleanup(self.tmp.cleanup)

    def test_missing_shift_arguments(self):
        code = cli_main(["time", "-i", str(self.root / "in")])
        self.assertEqual(code, 2)

    def test_unsigned_offset_is_rejected(self):
        code = cli_main(
            ["time", "-i", str(self.root / "in"), "--from-offset=10:00", "--to-offset=-07:00"]
        )
        self.assertEqual(code, 2)

    def test_zero_shift_without_offset_is_rejected(self):
        code = cli_main(["time", "-i", str(self.root / "in"), "--shift", "+00:00:00"])
        self.assertEqual(code, 2)

    def test_dry_run_does_not_copy(self):
        src = self.root / "in" / "a.arw"
        src.write_bytes(b"raw")

        code = cli_main(
            [
                "time",
                "-i",
                str(self.root / "in"),
                "-o",
                str(self.root / "out"),
                "--shift",
                "+01:00:00",
                "--dry-run",
            ]
        )

        self.assertEqual(code, 0)
        self.assertFalse((self.root / "out").exists())


if __name__ == "__main__":
    unittest.main()
