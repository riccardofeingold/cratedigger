import http.server
import shutil
import tempfile
import threading
import time
import unittest
from functools import partial
from pathlib import Path

import requests

from cratedigger.config import SettingsStore
from cratedigger.ffmpeg import find_ffmpeg
from cratedigger.library import Library
from cratedigger.models import Track
from cratedigger.services import AnalysisQueue, JobManager
from cratedigger.sources.base import Cancelled, DownloadResult, Source, SourceError, download_candidates

from .helpers import make_audio_file

try:
    import yt_dlp  # noqa: F401

    HAVE_YTDLP = True
except ImportError:
    HAVE_YTDLP = False

try:
    import mutagen  # noqa: F401

    HAVE_MUTAGEN = True
except ImportError:
    HAVE_MUTAGEN = False


class _Handler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):  # keep test output quiet
        pass

    def guess_type(self, path):
        if str(path).endswith(".html"):
            return "text/html"
        return "application/octet-stream" if str(path).endswith(".flac") else super().guess_type(path)


class _QuietServer(http.server.ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        pass  # clients hanging up early (the cancel tests) are expected


class LocalServer:
    """Serves a temp folder over HTTP on 127.0.0.1."""

    def __init__(self, folder: Path):
        self.httpd = _QuietServer(("127.0.0.1", 0), partial(_Handler, directory=str(folder)))
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()

    def url(self, name: str) -> str:
        return f"http://127.0.0.1:{self.httpd.server_address[1]}/{name}"


def local_session() -> requests.Session:
    s = requests.Session()
    s.trust_env = False  # never route localhost through a proxy
    return s


@unittest.skipUnless(find_ffmpeg(), "ffmpeg not available")
class CandidateDownloadTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.served = Path(cls.tmp.name) / "served"
        cls.served.mkdir()
        make_audio_file(cls.served / "song.mp3", find_ffmpeg(), seconds=20)
        shutil.copy(cls.served / "song.mp3", cls.served / "fake.flac")  # claims FLAC, is MP3
        (cls.served / "page.html").write_text("<html>login</html>")
        cls.server = LocalServer(cls.served).__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.server.__exit__()
        cls.tmp.cleanup()

    def setUp(self):
        self.work = Path(tempfile.mkdtemp())
        self.session = local_session()
        self.progress = []

    def tearDown(self):
        shutil.rmtree(self.work, ignore_errors=True)

    def _cb(self, done, total, stage=None):
        self.progress.append((done, total))

    def test_falls_back_when_lossless_file_is_not_lossless(self):
        candidates = [{"ext": ".flac", "url": self.server.url("fake.flac"), "lossless": True},
                      {"ext": ".mp3", "url": self.server.url("song.mp3"), "lossless": False}]
        path = download_candidates(self.session, candidates, self.work, self._cb, threading.Event(), "test")
        self.assertEqual(path.suffix, ".mp3")
        self.assertEqual(path.stat().st_size, (self.served / "song.mp3").stat().st_size)
        self.assertTrue(self.progress and self.progress[-1][0] == self.progress[-1][1])
        self.assertEqual(len(list(self.work.iterdir())), 1)  # the rejected file was removed

    def test_errors_are_reported(self):
        with self.assertRaisesRegex(SourceError, "web page"):
            download_candidates(self.session, [{"ext": ".mp3", "url": self.server.url("page.html")}],
                                self.work, self._cb, threading.Event(), "test")
        with self.assertRaisesRegex(SourceError, "404"):
            download_candidates(self.session, [{"ext": ".mp3", "url": self.server.url("missing.mp3")}],
                                self.work, self._cb, threading.Event(), "test")

    def test_cancel(self):
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(Cancelled):
            download_candidates(self.session, [{"ext": ".mp3", "url": self.server.url("song.mp3")}],
                                self.work, self._cb, cancel, "test")

    @unittest.skipUnless(HAVE_YTDLP, "yt-dlp not installed")
    def test_ytdlp_pipeline_converts_to_mp3(self):
        from cratedigger.sources.youtube import download_audio

        make_audio_file(self.served / "clip.m4a", find_ffmpeg(), seconds=12)
        options = {"quiet": True, "no_warnings": True, "noprogress": True, "ffmpeg_location": find_ffmpeg(),
                   "proxy": ""}
        path, info = download_audio(self.server.url("clip.m4a"), self.work, self._cb, threading.Event(), options)
        self.assertEqual(path.suffix, ".mp3")
        from cratedigger.audiofile import sniff_audio_ext

        self.assertEqual(sniff_audio_ext(path), ".mp3")
        self.assertGreater(path.stat().st_size, 100_000)  # 12 s at 320 kbps is ~480 kB
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(Cancelled):
            download_audio(self.server.url("clip.m4a"), Path(tempfile.mkdtemp()), self._cb, cancel, options)


class FakeSource(Source):
    """Downloads a synthetic file over local HTTP, like Jamendo/IA do."""

    id = "archive"
    name = "Fake"

    def __init__(self, session, url):
        self.session, self.url = session, url

    def download(self, track, workdir, progress, cancel, settings):
        path = download_candidates(self.session, [{"ext": ".mp3", "url": self.url}], workdir, progress, cancel, "Fake")
        return DownloadResult(path=path, tags={"year": "2020"})


@unittest.skipUnless(find_ffmpeg() and HAVE_MUTAGEN, "needs ffmpeg and mutagen")
class JobManagerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        served = root / "served"
        served.mkdir()
        make_audio_file(served / "song.mp3", find_ffmpeg(), seconds=30, bpm=127)
        self.server = LocalServer(served).__enter__()
        self.store = SettingsStore(root / "settings.json")
        self.store.update({"library_dir": str(root / "Library")})
        self.library = Library(root / "library.sqlite3")
        self.analysis = AnalysisQueue(self.library)
        session = local_session()
        self.jobs = JobManager(self.library, self.store, {"archive": FakeSource(session, self.server.url("song.mp3"))},
                               self.analysis, session)
        self.track = Track(source="archive", source_id="item/01", title="Harbour: Lights", artist="Low Tide",
                           album="Night Ferries", license="CC BY 4.0", page_url="https://example.org/item")

    def tearDown(self):
        self.jobs.shutdown(wait=True)
        self.analysis.shutdown(wait=True)
        self.server.__exit__()
        self.library.close()
        self.tmp.cleanup()

    def wait(self, predicate, timeout=60):
        end = time.time() + timeout
        while time.time() < end:
            if predicate():
                return True
            time.sleep(0.1)
        self.fail("timed out")

    def test_download_tags_files_and_analyses(self):
        from cratedigger.tagging import read_tags

        job = self.jobs.submit(self.track)
        self.assertIs(self.jobs.submit(self.track), job)  # same track while active: same job
        self.wait(lambda: job.state in ("done", "error"))
        self.assertEqual(job.state, "done", job.message)
        row = self.library.get(job.track_id)
        path = Path(row["path"])
        self.assertEqual(path.name, "Low Tide - Harbour - Lights.mp3")
        self.assertEqual(path.parent, Path(self.store.get().library_dir).resolve())
        tags = read_tags(path)
        self.assertEqual((tags["title"], tags["artist"], tags["year"]), ("Harbour: Lights", "Low Tide", "2020"))

        self.wait(lambda: self.library.get(job.track_id)["analysis_status"] in ("done", "error"))
        row = self.library.get(job.track_id)
        self.assertEqual(row["analysis_status"], "done", row["analysis_error"])
        self.assertAlmostEqual(row["bpm"], 127.0, delta=0.3)
        self.assertEqual(row["camelot"], "8A")
        self.assertEqual(len(row["waveform"]), 240)
        self.assertEqual(read_tags(path)["bpm"], 127.0)

        again = self.jobs.submit(self.track)
        self.assertEqual((again.state, again.message), ("done", "Already in your crate"))

    def test_name_clash_gets_a_number(self):
        lib_dir = Path(self.store.get().library_dir)
        lib_dir.mkdir(parents=True, exist_ok=True)
        (lib_dir / "Low Tide - Harbour - Lights.mp3").write_bytes(b"someone else's file")
        job = self.jobs.submit(self.track)
        self.wait(lambda: job.state in ("done", "error"))
        self.assertTrue(self.library.get(job.track_id)["path"].endswith("Low Tide - Harbour - Lights (2).mp3"))

    def test_unknown_source_fails_cleanly(self):
        job = self.jobs.submit(Track(source="nowhere", source_id="1", title="x", artist="y"))
        self.wait(lambda: job.state in ("done", "error"))
        self.assertEqual(job.state, "error")
        self.assertIn("Unknown source", job.message)


if __name__ == "__main__":
    unittest.main()
