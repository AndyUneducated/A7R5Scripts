import io
import pathlib
import tempfile
import unittest

from PIL import ExifTags, Image

from a7r5 import imaging

ORIENTATION = 0x0112
PIXEL_X = 0xA002


def noisy_image(size=(600, 400)) -> Image.Image:
    """An image that does not compress to nothing, so size limits are real."""
    import random

    random.seed(1234)
    img = Image.new("RGB", size)
    img.putdata(
        [
            (random.randint(0, 255), random.randint(0, 255), random.randint(0, 255))
            for _ in range(size[0] * size[1])
        ]
    )
    return img


class FlattenToRgbTest(unittest.TestCase):
    def test_alpha_composited_onto_background(self):
        img = Image.new("RGBA", (4, 4), (255, 0, 0, 0))
        white = imaging.flatten_to_rgb(img, (255, 255, 255))
        self.assertEqual(white.mode, "RGB")
        self.assertEqual(white.getpixel((0, 0)), (255, 255, 255))
        black = imaging.flatten_to_rgb(img, (0, 0, 0))
        self.assertEqual(black.getpixel((0, 0)), (0, 0, 0))

    def test_grayscale_becomes_rgb(self):
        self.assertEqual(imaging.flatten_to_rgb(Image.new("L", (4, 4))).mode, "RGB")

    def test_palette_transparency(self):
        img = Image.new("P", (4, 4))
        img.info["transparency"] = 0
        self.assertEqual(imaging.flatten_to_rgb(img, (0, 0, 0)).mode, "RGB")


class DownscaleTest(unittest.TestCase):
    def test_longest_edge_capped(self):
        img = Image.new("RGB", (1000, 500))
        self.assertEqual(imaging.downscale(img, 200).size, (200, 100))

    def test_no_upscale(self):
        img = Image.new("RGB", (100, 50))
        self.assertEqual(imaging.downscale(img, 900).size, (100, 50))

    def test_none_means_unlimited(self):
        img = Image.new("RGB", (100, 50))
        self.assertIs(imaging.downscale(img, None), img)


class DecodeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def test_orientation_is_baked_in(self):
        exif = Image.Exif()
        exif[ORIENTATION] = 6  # rotate 90 CW when displaying
        path = self.root / "rotated.jpg"
        Image.new("RGB", (100, 50)).save(path, exif=exif.tobytes())

        decoded = imaging.decode(path)
        self.assertEqual(decoded.image.size, (50, 100))

        carried = Image.Exif()
        carried.load(decoded.metadata.exif)
        self.assertNotIn(ORIENTATION, carried)

    def test_exif_is_carried_over(self):
        exif = Image.Exif()
        exif[0x0110] = "ILCE-7RM5"
        path = self.root / "tagged.jpg"
        Image.new("RGB", (20, 10)).save(path, exif=exif.tobytes())

        decoded = imaging.decode(path)
        loaded = Image.Exif()
        loaded.load(decoded.metadata.exif)
        self.assertEqual(loaded[0x0110], "ILCE-7RM5")

    def test_png_alpha_is_flattened(self):
        path = self.root / "alpha.png"
        Image.new("RGBA", (10, 10), (255, 0, 0, 0)).save(path)
        decoded = imaging.decode(path, bg_rgb=(0, 0, 0))
        self.assertEqual(decoded.image.mode, "RGB")
        self.assertEqual(decoded.image.getpixel((0, 0)), (0, 0, 0))


class EncodeTest(unittest.TestCase):
    def test_jpeg_roundtrip_with_exif(self):
        exif = Image.Exif()
        exif[0x0110] = "ILCE-7RM5"
        exif[ORIENTATION] = 8
        payload = imaging.encode(
            Image.new("RGB", (40, 20)),
            "jpg",
            80,
            imaging.Metadata(exif=exif.tobytes()),
        )
        with Image.open(io.BytesIO(payload)) as img:
            self.assertEqual(img.format, "JPEG")
            self.assertEqual(img.size, (40, 20))
            written = img.getexif()
            self.assertEqual(written[0x0110], "ILCE-7RM5")
            self.assertNotIn(ORIENTATION, written)
            self.assertEqual(written.get_ifd(ExifTags.IFD.Exif)[PIXEL_X], 40)

    def test_higher_quality_is_larger(self):
        img = noisy_image((200, 200))
        small = imaging.encode(img, "jpg", 40)
        large = imaging.encode(img, "jpg", 92)
        self.assertLess(len(small), len(large))

    def test_xmp_is_carried_over(self):
        xmp = b'<x:xmpmeta xmlns:x="adobe:ns:meta/"></x:xmpmeta>'
        payload = imaging.encode(
            Image.new("RGB", (40, 20)), "jpg", 80, imaging.Metadata(xmp=xmp)
        )
        with Image.open(io.BytesIO(payload)) as img:
            self.assertTrue(img.info.get("xmp"))

    @unittest.skipUnless(imaging.HEIF_ENABLED, "pillow-heif unavailable")
    def test_heif_roundtrip_with_exif(self):
        exif = Image.Exif()
        exif[0x0110] = "ILCE-7RM5"
        payload = imaging.encode(
            Image.new("RGB", (40, 20)),
            "heif",
            70,
            imaging.Metadata(exif=exif.tobytes()),
        )
        with Image.open(io.BytesIO(payload)) as img:
            self.assertEqual(img.size, (40, 20))
            self.assertEqual(img.getexif()[0x0110], "ILCE-7RM5")

    def test_no_metadata_means_no_metadata(self):
        """Encoders must not smuggle metadata out of Image.info."""
        exif = Image.Exif()
        exif[0x0110] = "ILCE-7RM5"
        formats = ["jpg"] + (["heif"] if imaging.HEIF_ENABLED else [])
        for out_format in formats:
            with self.subTest(out_format=out_format):
                img = Image.new("RGB", (40, 20))
                img.info["exif"] = exif.tobytes()
                img.info["xmp"] = b"<x:xmpmeta/>"
                img.info["icc_profile"] = b"junk-profile"

                payload = imaging.encode(img, out_format, 70)

                with Image.open(io.BytesIO(payload)) as out:
                    self.assertEqual(dict(out.getexif()), {})
                    self.assertIsNone(out.info.get("xmp"))
                    self.assertIsNone(out.info.get("icc_profile"))


class FitUnderMaxBytesTest(unittest.TestCase):
    def test_lowers_quality_before_resolution(self):
        img = noisy_image((400, 400))
        limit = len(imaging.encode(img, "jpg", 95)) // 2
        payload = imaging.fit_under_max_bytes(img, "jpg", limit, 95)
        self.assertLessEqual(len(payload), limit)
        with Image.open(io.BytesIO(payload)) as out:
            self.assertEqual(out.size, (400, 400))

    def test_downscales_when_quality_is_not_enough(self):
        img = noisy_image((900, 900))
        limit = 12_000
        payload = imaging.fit_under_max_bytes(img, "jpg", limit, 95)
        self.assertLessEqual(len(payload), limit)
        with Image.open(io.BytesIO(payload)) as out:
            self.assertLess(max(out.size), 900)

    def test_impossible_limit_raises_instead_of_writing(self):
        with self.assertRaises(RuntimeError):
            imaging.fit_under_max_bytes(noisy_image((800, 800)), "jpg", 200, 95)

    def test_exif_dimensions_match_the_downscaled_result(self):
        exif = Image.Exif()
        exif[0x0110] = "ILCE-7RM5"
        sub = exif.get_ifd(ExifTags.IFD.Exif)
        sub[PIXEL_X] = 900
        payload = imaging.fit_under_max_bytes(
            noisy_image((900, 900)),
            "jpg",
            12_000,
            95,
            imaging.Metadata(exif=exif.tobytes()),
        )
        with Image.open(io.BytesIO(payload)) as out:
            written = out.getexif().get_ifd(ExifTags.IFD.Exif)
            self.assertEqual(written[PIXEL_X], out.size[0])


if __name__ == "__main__":
    unittest.main()
