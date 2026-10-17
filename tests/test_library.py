import tempfile
import unittest
from pathlib import Path

from cratedigger.ffmpeg import find_ffmpeg
from cratedigger.library import Library

from .helpers import make_audio_file

try:
    import mutagen  # noqa: F401

    HAVE_MUTAGEN = True
except ImportError:
    HAVE_MUTAGEN = False


class LibraryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.lib = Library(self.root / "db" / "library.sqlite3")

    def tearDown(self):
        self.lib.close()
        self.tmp.cleanup()

    def _file(self, name: str) -> Path:
        p = self.root / name
        p.write_bytes(b"ID3fake")
        return p

    def test_add_update_get(self):
        track_id = self.lib.add(self._file("a.mp3"), source="jamendo", source_id="42", title="Harbour Lights",
                                artist="Low Tide", waveform=[0.1, 1.0])
        row = self.lib.get(track_id)
        self.assertEqual((row["title"], row["analysis_status"], row["waveform"]), ("Harbour Lights", "pending", [0.1, 1.0]))
        self.lib.update(track_id, bpm=124.0, camelot="8A", musical_key="Am", analysis_status="done", unknown="x")
        listed = self.lib.list_tracks()[0]
        self.assertEqual((listed["bpm"], listed["camelot"]), (124.0, "8A"))
        self.assertNotIn("waveform", listed)
        self.assertEqual(self.lib.known_uids(), {"jamendo:42"})
        self.assertEqual(self.lib.find_by_source("jamendo", "42")["id"], track_id)

    def test_same_path_updates_instead_of_duplicating(self):
        p = self._file("a.mp3")
        first = self.lib.add(p, title="One")
        second = self.lib.add(p, title="Two")
        self.assertEqual(first, second)
        self.assertEqual(len(self.lib.list_tracks()), 1)
        self.assertEqual(self.lib.get(first)["title"], "Two")

    def test_prune_missing(self):
        p = self._file("gone.mp3")
        self.lib.add(p, title="Gone")
        p.unlink()
        self.assertEqual(self.lib.prune_missing(), 1)
        self.assertEqual(self.lib.list_tracks(), [])

    @unittest.skipUnless(find_ffmpeg() and HAVE_MUTAGEN, "needs ffmpeg and mutagen")
    def test_rescan_imports_files_and_existing_tags(self):
        from cratedigger.tagging import write_tags

        folder = self.root / "music"
        (folder / "sub").mkdir(parents=True)
        (folder / ".hidden").mkdir()
        tagged = make_audio_file(folder / "sub" / "Low Tide - Undertow.mp3", find_ffmpeg(), seconds=10)
        write_tags(tagged, {"title": "Undertow", "artist": "Low Tide", "bpm": 126.4, "key": "F#m"})
        make_audio_file(folder / "02 - Mira - Ferry Song.flac", find_ffmpeg(), seconds=10)
        make_audio_file(folder / ".hidden" / "skip.mp3", find_ffmpeg(), seconds=10)
        (folder / "notes.txt").write_text("not audio")

        result = self.lib.rescan(folder)
        self.assertEqual(result["added"], 2)
        rows = {r["title"]: r for r in self.lib.list_tracks()}
        self.assertEqual(rows["Undertow"]["bpm"], 126.0)  # tags store whole BPMs
        self.assertEqual((rows["Undertow"]["camelot"], rows["Undertow"]["musical_key"]), ("11A", "F#m"))
        self.assertEqual(rows["Ferry Song"]["artist"], "Mira")
        self.assertEqual(len(result["to_analyze"]), 2)
        self.assertEqual(self.lib.rescan(folder)["added"], 0)


if __name__ == "__main__":
    unittest.main()
