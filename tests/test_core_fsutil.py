import pathlib
import tempfile
import unittest

from mediakit.core import fsutil


class NormalizeExtsTest(unittest.TestCase):
    def test_normalization(self):
        self.assertEqual(
            fsutil.normalize_exts(["JPG", ".arw", " mp4 ", ""]),
            {".jpg", ".arw", ".mp4"},
        )


class IterFilesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def touch(self, relative: str) -> pathlib.Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
        return path

    def test_matches_extensions_recursively(self):
        self.touch("a.ARW")
        self.touch("day1/b.jpg")
        self.touch("day1/notes.txt")
        found = {p.name for p in fsutil.iter_files(self.root, {".arw", ".jpg"})}
        self.assertEqual(found, {"a.ARW", "b.jpg"})

    def test_skips_appledouble_and_hidden(self):
        self.touch("._DSC1.ARW")
        self.touch(".hidden.arw")
        self.touch("real.arw")
        self.touch(".cache/inside.arw")
        found = {p.name for p in fsutil.iter_files(self.root, {".arw"})}
        self.assertEqual(found, {"real.arw"})

    def test_excludes_output_subtree(self):
        self.touch("in.arw")
        self.touch("output/old.arw")
        self.touch("output/nested/older.arw")
        found = {
            p.name
            for p in fsutil.iter_files(
                self.root, {".arw"}, exclude_dirs=[self.root / "output"]
            )
        }
        self.assertEqual(found, {"in.arw"})


class MirrorPathTest(unittest.TestCase):
    def test_preserves_structure(self):
        result = fsutil.mirror_path("in/day1/DSC1.ARW", "in", "out")
        self.assertEqual(result, pathlib.Path("out/day1/DSC1.ARW"))

    def test_renames(self):
        result = fsutil.mirror_path("in/day1/DSC1.ARW", "in", "out", "DSC1.heic")
        self.assertEqual(result, pathlib.Path("out/day1/DSC1.heic"))

    def test_root_level_file(self):
        result = fsutil.mirror_path("in/DSC1.ARW", "in", "out")
        self.assertEqual(result, pathlib.Path("out/DSC1.ARW"))


class ParseSizeTest(unittest.TestCase):
    def test_units_are_binary(self):
        self.assertEqual(fsutil.parse_size("3mb"), 3 * 1024**2)
        self.assertEqual(fsutil.parse_size("3MiB"), 3 * 1024**2)
        self.assertEqual(fsutil.parse_size("1500kb"), 1500 * 1024)
        self.assertEqual(fsutil.parse_size("2.5m"), int(2.5 * 1024**2))
        self.assertEqual(fsutil.parse_size("3145728"), 3145728)

    def test_invalid(self):
        for text in ("", "0mb", "-3mb", "abc", "mb"):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    fsutil.parse_size(text)


class HumanBytesTest(unittest.TestCase):
    def test_formatting(self):
        self.assertEqual(fsutil.human_bytes(512), "512 B")
        self.assertEqual(fsutil.human_bytes(2048), "2.0 KiB")
        self.assertEqual(fsutil.human_bytes(5 * 1024**2), "5.0 MiB")


if __name__ == "__main__":
    unittest.main()
