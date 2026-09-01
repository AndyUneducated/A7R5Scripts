import pathlib
import tempfile
import unittest

from PIL import Image

from mediakit.cli import main as cli_main
from mediakit.photo import shrink as batch_shrink


def write_jpeg(path: pathlib.Path, size=(120, 80)) -> pathlib.Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, (30, 120, 200)).save(path, quality=95)
    return path


class OutputNameTest(unittest.TestCase):
    def test_source_ext_naming(self):
        name = batch_shrink.output_name(
            pathlib.Path("DSC1.ARW"), ".heic", "source-ext"
        )
        self.assertEqual(name, "DSC1.arw.heic")

    def test_plain_naming(self):
        name = batch_shrink.output_name(pathlib.Path("DSC1.ARW"), ".jpg", "plain")
        self.assertEqual(name, "DSC1.jpg")


class BuildPlanTest(unittest.TestCase):
    def test_mirrors_input_structure(self):
        inputs = [pathlib.Path("in/day1/DSC1.ARW"), pathlib.Path("in/day2/DSC1.ARW")]
        plan, warnings = batch_shrink.build_plan(
            inputs, pathlib.Path("in"), pathlib.Path("out"), ".heic", "plain"
        )
        self.assertEqual(warnings, [])
        self.assertEqual(
            [dst for _, dst in plan],
            [
                pathlib.Path("out/day1/DSC1.heic"),
                pathlib.Path("out/day2/DSC1.heic"),
            ],
        )

    def test_same_stem_in_one_folder_does_not_collide(self):
        inputs = [pathlib.Path("in/DSC1.ARW"), pathlib.Path("in/DSC1.JPG")]
        plan, warnings = batch_shrink.build_plan(
            inputs, pathlib.Path("in"), pathlib.Path("out"), ".heic", "plain"
        )
        destinations = [dst for _, dst in plan]
        self.assertEqual(len(set(destinations)), 2)
        self.assertEqual(len(warnings), 1)
        self.assertIn("DSC1.jpg.heic", str(destinations[1]))

    def test_source_ext_naming_is_collision_free(self):
        inputs = [pathlib.Path("in/DSC1.ARW"), pathlib.Path("in/DSC1.JPG")]
        plan, warnings = batch_shrink.build_plan(
            inputs, pathlib.Path("in"), pathlib.Path("out"), ".heic", "source-ext"
        )
        self.assertEqual(warnings, [])
        self.assertEqual(len({dst for _, dst in plan}), 2)


class ParseBgTest(unittest.TestCase):
    def test_named_and_numeric(self):
        self.assertEqual(batch_shrink.parse_bg("white"), (255, 255, 255))
        self.assertEqual(batch_shrink.parse_bg("BLACK"), (0, 0, 0))
        self.assertEqual(batch_shrink.parse_bg("10, 20,30"), (10, 20, 30))

    def test_invalid(self):
        for text in ("grey", "1,2", "1,2,300", "1,2,x", ""):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    batch_shrink.parse_bg(text)


class ProcessOneTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def options(self, **overrides):
        defaults = dict(
            out_format="jpg",
            max_edge=64,
            quality=80,
            max_bytes=None,
            strip=False,
            bg_rgb=(255, 255, 255),
            raw_wb="camera",
            raw_half_size=False,
            subsampling="auto",
            overwrite=False,
        )
        defaults.update(overrides)
        return batch_shrink.Options(**defaults)

    def test_converts_and_creates_parent_directories(self):
        src = write_jpeg(self.root / "in" / "day1" / "a.jpg")
        dst = self.root / "out" / "day1" / "a.jpg.jpg"

        result = batch_shrink.process_one(str(src), str(dst), self.options())

        self.assertEqual(result.status, batch_shrink.STATUS_OK)
        self.assertTrue(dst.exists())
        self.assertEqual(result.after, dst.stat().st_size)
        with Image.open(dst) as img:
            self.assertEqual(max(img.size), 64)

    def test_existing_output_is_skipped(self):
        src = write_jpeg(self.root / "in" / "a.jpg")
        dst = self.root / "out" / "a.jpg.jpg"
        dst.parent.mkdir(parents=True)
        dst.write_bytes(b"stale")

        result = batch_shrink.process_one(str(src), str(dst), self.options())
        self.assertEqual(result.status, batch_shrink.STATUS_SKIPPED)
        self.assertEqual(dst.read_bytes(), b"stale")

        result = batch_shrink.process_one(
            str(src), str(dst), self.options(overwrite=True)
        )
        self.assertEqual(result.status, batch_shrink.STATUS_OK)
        self.assertNotEqual(dst.read_bytes(), b"stale")

    def test_unreadable_input_reports_error(self):
        src = self.root / "in" / "broken.jpg"
        src.parent.mkdir(parents=True)
        src.write_bytes(b"not a jpeg")
        dst = self.root / "out" / "broken.jpg.jpg"

        result = batch_shrink.process_one(str(src), str(dst), self.options())
        self.assertEqual(result.status, batch_shrink.STATUS_ERROR)
        self.assertIn("broken.jpg", result.error)
        self.assertFalse(dst.exists())

    def test_impossible_max_size_leaves_no_output(self):
        src = write_jpeg(self.root / "in" / "a.jpg", size=(400, 400))
        dst = self.root / "out" / "a.jpg.jpg"

        result = batch_shrink.process_one(
            str(src), str(dst), self.options(max_edge=None, max_bytes=64)
        )
        self.assertEqual(result.status, batch_shrink.STATUS_ERROR)
        self.assertFalse(dst.exists())
        self.assertFalse(list(dst.parent.glob("*.part")))


class MainTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def test_end_to_end_jpeg(self):
        write_jpeg(self.root / "in" / "day1" / "a.jpg")
        write_jpeg(self.root / "in" / "day2" / "a.jpg")
        write_jpeg(self.root / "in" / "._a.jpg")

        code = cli_main(
            [
                "photo",
                "shrink",
                str(self.root / "in"),
                str(self.root / "out"),
                "--out-format",
                "jpg",
                "--max-edge",
                "50",
                "--workers",
                "1",
                "--naming",
                "plain",
            ]
        )

        self.assertEqual(code, 0)
        produced = sorted(
            str(p.relative_to(self.root / "out"))
            for p in (self.root / "out").rglob("*.jpg")
        )
        self.assertEqual(produced, ["day1/a.jpg", "day2/a.jpg"])

    def test_no_inputs(self):
        (self.root / "in").mkdir()
        code = cli_main(["photo", "shrink", str(self.root / "in"), str(self.root / "out")])
        self.assertEqual(code, 1)

    def test_bad_quality_exits_with_two(self):
        (self.root / "in").mkdir()
        with self.assertRaises(SystemExit) as ctx:
            cli_main(
                [
                    "photo",
                    "shrink",
                    str(self.root / "in"),
                    str(self.root / "out"),
                    "--out-format",
                    "jpg",
                    "--quality",
                    "99",
                ]
            )
        self.assertEqual(ctx.exception.code, 2)

    def test_output_inside_input_is_not_rescanned(self):
        write_jpeg(self.root / "in" / "a.jpg")
        out = self.root / "in" / "out"

        code = cli_main(
            [
                "photo",
                "shrink",
                str(self.root / "in"),
                str(out),
                "--out-format",
                "jpg",
                "--max-edge",
                "40",
                "--workers",
                "1",
            ]
        )
        self.assertEqual(code, 0)
        self.assertEqual([p.name for p in out.rglob("*.jpg")], ["a.jpg.jpg"])


if __name__ == "__main__":
    unittest.main()
