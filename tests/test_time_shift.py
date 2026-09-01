import unittest

from mediakit.time import timeshift


class ParseOffsetTest(unittest.TestCase):
    def test_signed_offsets(self):
        self.assertEqual(timeshift.parse_offset("-08:00"), -480)
        self.assertEqual(timeshift.parse_offset("+09:30"), 570)
        self.assertEqual(timeshift.parse_offset("+00:00"), 0)
        self.assertEqual(timeshift.parse_offset("Z"), 0)

    def test_unsigned_offset_is_rejected(self):
        # "10:00" used to be silently parsed as 0 minutes.
        for text in ("10:00", "08:00", "8", "", "+8:0", "+08:60"):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    timeshift.parse_offset(text)

    def test_out_of_range(self):
        with self.assertRaises(ValueError):
            timeshift.parse_offset("+15:00")

    def test_format_offset(self):
        self.assertEqual(timeshift.format_offset(-480), "-08:00")
        self.assertEqual(timeshift.format_offset(570), "+09:30")


class ParseShiftTest(unittest.TestCase):
    def test_seconds_are_kept(self):
        self.assertEqual(timeshift.parse_shift("+00:00:30"), 30)
        self.assertEqual(timeshift.parse_shift("-00:30:15"), -1815)
        self.assertEqual(timeshift.parse_shift("+01:00:00"), 3600)

    def test_seconds_are_optional(self):
        self.assertEqual(timeshift.parse_shift("+01:30"), 5400)

    def test_invalid(self):
        for text in ("01:00:00", "+1", "+01:60:00", "abc"):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    timeshift.parse_shift(text)


class ComputeShiftTest(unittest.TestCase):
    def test_explicit_shift_wins(self):
        self.assertEqual(
            timeshift.compute_shift_seconds("+02:00:00", "-08:00", "-07:00"), 7200
        )

    def test_offset_difference(self):
        self.assertEqual(
            timeshift.compute_shift_seconds(None, "-08:00", "-07:00"), 3600
        )
        self.assertEqual(
            timeshift.compute_shift_seconds(None, "+09:00", "-07:00"), -16 * 3600
        )

    def test_incomplete_offsets(self):
        with self.assertRaises(ValueError):
            timeshift.compute_shift_seconds(None, "-08:00", None)
        with self.assertRaises(ValueError):
            timeshift.compute_shift_seconds(None, None, None)


class ExiftoolExprTest(unittest.TestCase):
    def test_unambiguous_form(self):
        self.assertEqual(timeshift.exiftool_shift_expr(3600), "+=0:0:0 1:00:00")
        self.assertEqual(timeshift.exiftool_shift_expr(-1800), "-=0:0:0 0:30:00")
        self.assertEqual(timeshift.exiftool_shift_expr(30), "+=0:0:0 0:00:30")
        self.assertEqual(timeshift.exiftool_shift_expr(90061), "+=0:0:0 25:01:01")

    def test_format_shift(self):
        self.assertEqual(timeshift.format_shift(3661), "+01:01:01")
        self.assertEqual(timeshift.format_shift(-30), "-00:00:30")


if __name__ == "__main__":
    unittest.main()
