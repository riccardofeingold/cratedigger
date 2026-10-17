import unittest

from cratedigger.licenses import is_open_license, license_label
from cratedigger.naming import clean_channel_name, parse_filename, safe_filename, split_artist_title


class SafeFilenameTest(unittest.TestCase):
    def test_strips_characters_windows_rejects(self):
        self.assertEqual(safe_filename('Dub/Step: "Live" <Mix>?*'), "Dub-Step - Live Mix")

    def test_reserved_and_empty_names(self):
        self.assertEqual(safe_filename("CON"), "_CON")
        self.assertEqual(safe_filename("  ..  "), "Untitled")

    def test_truncates_without_trailing_dot_or_space(self):
        name = safe_filename("a" * 50 + " . " + "b" * 100, max_len=52)
        self.assertLessEqual(len(name), 52)
        self.assertFalse(name.endswith((" ", ".")))


class ArtistTitleTest(unittest.TestCase):
    def test_splits_and_drops_video_noise(self):
        self.assertEqual(split_artist_title("Night Driver - Neon Coast (Official Video) [HD]", "Some Channel"),
                         ("Night Driver", "Neon Coast"))

    def test_keeps_mix_names(self):
        self.assertEqual(split_artist_title("Night Driver – Neon Coast (Extended Mix)", ""),
                         ("Night Driver", "Neon Coast (Extended Mix)"))

    def test_pipe_suffix_removed(self):
        self.assertEqual(split_artist_title("Low Tide - Harbour Lights | Official Music Video", ""),
                         ("Low Tide", "Harbour Lights"))

    def test_falls_back_to_channel(self):
        self.assertEqual(split_artist_title("Harbour Lights [Original Mix]", "Low Tide - Topic"),
                         ("Low Tide", "Harbour Lights [Original Mix]"))
        self.assertEqual(clean_channel_name("LowTideVEVO"), "LowTide")
        self.assertEqual(split_artist_title("Untitled sketch", ""), ("Unknown artist", "Untitled sketch"))

    def test_hyphen_inside_names_is_not_a_separator(self):
        self.assertEqual(split_artist_title("Lo-Fi Beats to Study To", "Chill Hub"),
                         ("Chill Hub", "Lo-Fi Beats to Study To"))

    def test_parse_filename(self):
        self.assertEqual(parse_filename("01 - Low Tide - Harbour Lights"), ("Low Tide", "Harbour Lights"))
        self.assertEqual(parse_filename("03_harbour_lights"), ("Unknown artist", "harbour lights"))


class LicenseTest(unittest.TestCase):
    def test_labels(self):
        self.assertEqual(license_label("http://creativecommons.org/licenses/by-nc-sa/3.0/"), "CC BY-NC-SA 3.0")
        self.assertEqual(license_label("http://creativecommons.org/licenses/by-nc-nd/3.0/de/"), "CC BY-NC-ND 3.0")
        self.assertEqual(license_label("https://creativecommons.org/publicdomain/zero/1.0/"), "CC0")
        self.assertEqual(license_label("http://creativecommons.org/licenses/publicdomain/"), "Public domain")
        self.assertEqual(license_label("http://creativecommons.org/licenses/sampling+/1.0/"), "CC SAMPLING+ 1.0")
        self.assertEqual(license_label(""), "")

    def test_open_license(self):
        self.assertTrue(is_open_license("http://creativecommons.org/licenses/by/4.0/"))
        self.assertFalse(is_open_license("https://example.com/terms"))
        self.assertFalse(is_open_license(None))


if __name__ == "__main__":
    unittest.main()
