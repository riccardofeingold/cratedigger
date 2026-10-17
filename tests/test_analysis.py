import tempfile
import unittest
from pathlib import Path

import numpy as np

from cratedigger.analysis import (SR, AnalysisError, analyze_file, analyze_signal, camelot, decode_audio,
                                  fold_bpm, key_name, parse_key)
from cratedigger.ffmpeg import find_ffmpeg

from .helpers import make_audio_file, synth_track

CAMELOT_WHEEL = {  # the reference chart DJs use
    "C": "8B", "G": "9B", "D": "10B", "A": "11B", "E": "12B", "B": "1B", "F#": "2B", "Db": "3B",
    "Ab": "4B", "Eb": "5B", "Bb": "6B", "F": "7B",
    "Am": "8A", "Em": "9A", "Bm": "10A", "F#m": "11A", "C#m": "12A", "G#m": "1A", "Ebm": "2A",
    "Bbm": "3A", "Fm": "4A", "Cm": "5A", "Gm": "6A", "Dm": "7A",
}


class KeyNamesTest(unittest.TestCase):
    def test_camelot_matches_reference_wheel(self):
        for tonic in range(12):
            for mode in ("major", "minor"):
                name = key_name(tonic, mode)
                self.assertEqual(camelot(tonic, mode), CAMELOT_WHEEL[name], name)

    def test_parse_key_variants(self):
        self.assertEqual(parse_key("Am"), ("Am", "8A"))
        self.assertEqual(parse_key("A minor"), ("Am", "8A"))
        self.assertEqual(parse_key("F#m"), ("F#m", "11A"))
        self.assertEqual(parse_key("Gbm"), ("F#m", "11A"))
        self.assertEqual(parse_key("Bb"), ("Bb", "6B"))
        self.assertEqual(parse_key("bm"), ("Bm", "10A"))
        self.assertEqual(parse_key("08A"), ("Am", "8A"))
        self.assertEqual(parse_key("12B"), ("E", "12B"))
        self.assertEqual(parse_key("1m"), ("Am", "8A"))  # Traktor Open Key
        self.assertEqual(parse_key("1d"), ("C", "8B"))
        self.assertIsNone(parse_key("o"))
        self.assertIsNone(parse_key("13A"))
        self.assertIsNone(parse_key(""))

    def test_fold_bpm(self):
        self.assertEqual(fold_bpm(64.0), 128.0)
        self.assertEqual(fold_bpm(196.0), 98.0)
        self.assertEqual(fold_bpm(174.0), 174.0)


class SignalTest(unittest.TestCase):
    def test_tempo_on_synthetic_tracks(self):
        for bpm in (96.0, 118.0, 123.5, 128.0, 140.0, 172.0):
            with self.subTest(bpm=bpm):
                result = analyze_signal(synth_track(bpm=bpm, seconds=60, seed=int(bpm)), SR)
                self.assertAlmostEqual(result["bpm"], bpm, delta=0.3)

    def test_key_on_synthetic_tracks(self):
        for tonic, minor, expected in ((9, True, "Am"), (0, False, "C"), (2, True, "Dm"), (7, False, "G")):
            with self.subTest(key=expected):
                result = analyze_signal(synth_track(bpm=124, seconds=60, tonic=tonic, minor=minor), SR)
                self.assertEqual(result["key"], expected)
                self.assertEqual(result["camelot"], CAMELOT_WHEEL[expected])

    def test_waveform_and_duration(self):
        result = analyze_signal(synth_track(seconds=30), SR)
        self.assertEqual(len(result["waveform"]), 240)
        self.assertAlmostEqual(max(result["waveform"]), 1.0, places=2)
        self.assertAlmostEqual(result["duration"], 30.0, delta=0.05)

    def test_too_short(self):
        with self.assertRaises(AnalysisError):
            analyze_signal(np.zeros(SR * 3, dtype=np.float32), SR)

    def test_silence_has_no_key_or_tempo(self):
        result = analyze_signal(np.zeros(SR * 20, dtype=np.float32), SR)
        self.assertNotIn("bpm", result)
        self.assertNotIn("key", result)


@unittest.skipUnless(find_ffmpeg(), "ffmpeg not available")
class FileTest(unittest.TestCase):
    def test_decodes_and_analyses_encoded_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            for ext in (".mp3", ".flac", ".ogg", ".m4a"):
                with self.subTest(ext=ext):
                    path = make_audio_file(Path(tmp) / f"track{ext}", find_ffmpeg(), seconds=30, bpm=126)
                    result = analyze_file(path)
                    self.assertAlmostEqual(result["bpm"], 126.0, delta=0.3)
                    self.assertEqual(result["key"], "Am")

    def test_garbage_file_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "not-audio.mp3"
            bad.write_bytes(b"this is not audio" * 100)
            with self.assertRaises(AnalysisError):
                decode_audio(bad)


if __name__ == "__main__":
    unittest.main()
