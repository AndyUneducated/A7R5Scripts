import io
import unittest

from PIL import ExifTags, Image

from mediakit.photo import exifmeta

ORIENTATION = 0x0112
MODEL = 0x0110
DATE_TIME_ORIGINAL = 0x9003
PIXEL_X = 0xA002
PIXEL_Y = 0xA003
MAKER_NOTE = 0x927C


def sample_exif(orientation: int = 6, size: tuple[int, int] = (400, 300)) -> bytes:
    exif = Image.Exif()
    exif[MODEL] = "ILCE-7RM5"
    exif[ORIENTATION] = orientation
    sub = exif.get_ifd(ExifTags.IFD.Exif)
    sub[DATE_TIME_ORIGINAL] = "2026:08:31 12:00:00"
    sub[PIXEL_X], sub[PIXEL_Y] = size
    sub[MAKER_NOTE] = b"\x00" * 32
    gps = exif.get_ifd(ExifTags.IFD.GPSInfo)
    gps[1] = "N"
    return exif.tobytes()


def load(exif_bytes: bytes) -> Image.Exif:
    exif = Image.Exif()
    exif.load(exif_bytes)
    return exif


class NormalizeExifTest(unittest.TestCase):
    def test_none_and_empty(self):
        self.assertIsNone(exifmeta.normalize_exif(None, (10, 10)))
        self.assertIsNone(exifmeta.normalize_exif(b"", (10, 10)))

    def test_orientation_dropped(self):
        result = exifmeta.normalize_exif(sample_exif(), (200, 150))
        self.assertNotIn(ORIENTATION, load(result))

    def test_dimensions_updated(self):
        result = exifmeta.normalize_exif(sample_exif(size=(400, 300)), (200, 150))
        sub = load(result).get_ifd(ExifTags.IFD.Exif)
        self.assertEqual((sub[PIXEL_X], sub[PIXEL_Y]), (200, 150))

    def test_camera_and_capture_time_survive(self):
        result = exifmeta.normalize_exif(sample_exif(), (200, 150))
        exif = load(result)
        self.assertEqual(exif[MODEL], "ILCE-7RM5")
        self.assertEqual(
            exif.get_ifd(ExifTags.IFD.Exif)[DATE_TIME_ORIGINAL],
            "2026:08:31 12:00:00",
        )
        self.assertEqual(exif.get_ifd(ExifTags.IFD.GPSInfo)[1], "N")

    def test_maker_note_dropped(self):
        result = exifmeta.normalize_exif(sample_exif(), (200, 150))
        self.assertNotIn(MAKER_NOTE, load(result).get_ifd(ExifTags.IFD.Exif))

    def test_garbage_is_ignored(self):
        self.assertIsNone(exifmeta.normalize_exif(b"not exif at all", (10, 10)))

    def test_creates_exif_ifd_when_missing(self):
        exif = Image.Exif()
        exif[MODEL] = "ILCE-7RM5"
        result = exifmeta.normalize_exif(exif.tobytes(), (64, 48))
        sub = load(result).get_ifd(ExifTags.IFD.Exif)
        self.assertEqual((sub[PIXEL_X], sub[PIXEL_Y]), (64, 48))


class ExifFromJpegTest(unittest.TestCase):
    def test_reads_embedded_exif(self):
        buffer = io.BytesIO()
        Image.new("RGB", (8, 8)).save(buffer, format="JPEG", exif=sample_exif())
        exif_bytes = exifmeta.exif_from_jpeg_bytes(buffer.getvalue())
        self.assertIsNotNone(exif_bytes)
        self.assertEqual(load(exif_bytes)[MODEL], "ILCE-7RM5")

    def test_jpeg_without_exif(self):
        buffer = io.BytesIO()
        Image.new("RGB", (8, 8)).save(buffer, format="JPEG")
        self.assertIsNone(exifmeta.exif_from_jpeg_bytes(buffer.getvalue()))

    def test_not_an_image(self):
        self.assertIsNone(exifmeta.exif_from_jpeg_bytes(b"nope"))


if __name__ == "__main__":
    unittest.main()
