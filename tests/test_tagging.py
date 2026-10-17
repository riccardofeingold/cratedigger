import tempfile
import unittest
from pathlib import Path

from cratedigger.audiofile import sniff_audio_ext
from cratedigger.ffmpeg import find_ffmpeg

from .helpers import FAKE_JPEG, make_audio_file

try:
    import mutagen  # noqa: F401

    HAVE_MUTAGEN = True
except ImportError:
    HAVE_MUTAGEN = False


@unittest.skipUnless(find_ffmpeg(), "ffmpeg not available")
class SniffTest(unittest.TestCase):
    def test_sniffs_real_formats(self):
        with tempfile.TemporaryDirectory() as tmp:
            for ext in (".mp3", ".flac", ".ogg", ".m4a", ".wav"):
                path = make_audio_file(Path(tmp) / f"x{ext}", find_ffmpeg(), seconds=2)
                renamed = path.rename(path.with_suffix(".bin"))
                self.assertEqual(sniff_audio_ext(renamed), ext)
            junk = Path(tmp) / "page.mp3"
            junk.write_text("<!doctype html><html>nope</html>")
            self.assertIsNone(sniff_audio_ext(junk))


@unittest.skipUnless(find_ffmpeg() and HAVE_MUTAGEN, "needs ffmpeg and mutagen")
class TagRoundTripTest(unittest.TestCase):
    def test_write_then_read(self):
        from cratedigger.tagging import read_tags, write_tags

        fields = {"title": "Harbour Lights", "artist": "Low Tide", "album": "Night Ferries", "genre": "techno",
                  "year": "2021", "comment": "CC BY 3.0 | https://example.org/track/1",
                  "url": "https://example.org/track/1", "license_url": "https://creativecommons.org/licenses/by/3.0/",
                  "bpm": 123.6, "key": "C#m"}
        with tempfile.TemporaryDirectory() as tmp:
            for ext in (".mp3", ".flac", ".ogg", ".m4a", ".wav"):
                with self.subTest(ext=ext):
                    path = make_audio_file(Path(tmp) / f"track{ext}", find_ffmpeg(), seconds=3)
                    self.assertTrue(write_tags(path, fields, cover=FAKE_JPEG))
                    tags = read_tags(path)
                    self.assertEqual(tags["title"], "Harbour Lights")
                    self.assertEqual(tags["artist"], "Low Tide")
                    self.assertEqual(tags["album"], "Night Ferries")
                    self.assertEqual(tags["year"], "2021")
                    self.assertEqual(tags["bpm"], 124.0)
                    self.assertEqual(tags["key"], "C#m")
                    self.assertAlmostEqual(tags["duration"], 3.0, delta=0.15)
                    # Only the given fields change.
                    write_tags(path, {"bpm": 62})
                    again = read_tags(path)
                    self.assertEqual((again["bpm"], again["title"], again["key"]), (62.0, "Harbour Lights", "C#m"))

    def test_mp3_is_id3v23_with_cover(self):
        from mutagen.id3 import ID3

        from cratedigger.tagging import write_tags

        with tempfile.TemporaryDirectory() as tmp:
            path = make_audio_file(Path(tmp) / "t.mp3", find_ffmpeg(), seconds=2)
            write_tags(path, {"title": "Ünïcødé ✓", "bpm": 128}, cover=FAKE_JPEG)
            tags = ID3(path)
            self.assertEqual(tags.version[:2], (2, 3))
            self.assertEqual(str(tags["TIT2"]), "Ünïcødé ✓")
            self.assertEqual(tags.getall("APIC")[0].data, FAKE_JPEG)

    def test_unknown_format_is_skipped(self):
        from cratedigger.tagging import write_tags

        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "x.xyz"
            p.write_bytes(b"data")
            self.assertFalse(write_tags(p, {"title": "x"}))


if __name__ == "__main__":
    unittest.main()
