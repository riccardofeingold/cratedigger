import shutil
import tempfile
import time
import unittest
from pathlib import Path

from cratedigger.config import SettingsStore
from cratedigger.ffmpeg import find_ffmpeg
from cratedigger.library import Library
from cratedigger.models import Track
from cratedigger.server import create_app
from cratedigger.services import AnalysisQueue, JobManager
from cratedigger.sources.base import DownloadResult, Source, SourceError

from .helpers import make_audio_file

TOKEN = "test-token"


class ReadySource(Source):
    id = "archive"
    name = "Internet Archive"

    def __init__(self, ffmpeg):
        self.ffmpeg = ffmpeg

    def search(self, query, *, cc_only=False):
        return [Track(source="archive", source_id="rel/01", title=f"{query} one", artist="Low Tide", duration=200,
                      license="CC BY 4.0", page_url="https://example.org/rel",
                      downloads=[{"ext": ".mp3", "url": "https://example.org/secret.mp3", "lossless": False}])]

    def download(self, track, workdir, progress, cancel, settings):
        path = make_audio_file(Path(workdir) / "dl.mp3", self.ffmpeg, seconds=15, bpm=122)
        progress(10, 10)
        return DownloadResult(path=path)


class OffSource(Source):
    id = "youtube"
    name = "YouTube"

    def status(self):
        return False, "Install yt-dlp to search YouTube"


class BrokenSource(Source):
    id = "jamendo"
    name = "Jamendo"

    def search(self, query, *, cc_only=False):
        raise SourceError("Jamendo is down")


@unittest.skipUnless(find_ffmpeg(), "ffmpeg not available")
class ServerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        root = Path(self.tmp)
        self.store = SettingsStore(root / "settings.json")
        self.store.update({"library_dir": str(root / "Library")})
        self.library = Library(root / "library.sqlite3")
        self.analysis = AnalysisQueue(self.library)
        self.sources = {"youtube": OffSource(), "jamendo": BrokenSource(), "archive": ReadySource(find_ffmpeg())}
        self.jobs = JobManager(self.library, self.store, self.sources, self.analysis, session=None)
        self.revealed = []
        app = create_app(settings_store=self.store, library=self.library, sources=self.sources, jobs=self.jobs,
                         analysis=self.analysis, token=TOKEN,
                         file_actions={"reveal": self.revealed.append, "open": self.revealed.append})
        app.testing = True
        self.client = app.test_client()
        self.headers = {"X-Crate-Token": TOKEN}

    def tearDown(self):
        self.jobs.shutdown(wait=True)
        self.analysis.shutdown(wait=True)
        self.library.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def get(self, url, **kw):
        return self.client.get(url, headers=self.headers, **kw)

    def post(self, url, json=None):
        return self.client.post(url, headers=self.headers, json=json)

    def wait(self, predicate, timeout=60):
        end = time.time() + timeout
        while time.time() < end:
            if predicate():
                return
            time.sleep(0.1)
        self.fail("timed out")

    def test_index_and_security(self):
        page = self.client.get("/")
        self.assertEqual(page.status_code, 200)
        self.assertIn(TOKEN.encode(), page.data)
        self.assertIn("frame-src https://www.youtube-nocookie.com", page.headers["Content-Security-Policy"])
        self.assertEqual(self.client.get("/", headers={"Host": "evil.example:8765"}).status_code, 403)
        self.assertEqual(self.client.get("/api/status").status_code, 403)
        self.assertEqual(self.client.get("/api/status", headers={"X-Crate-Token": "wrong"}).status_code, 403)
        self.assertEqual(self.client.get(f"/api/status?t={TOKEN}").status_code, 200)
        self.assertEqual(self.client.get("/health").json["app"], "cratedigger")
        script = self.client.get("/static/app.js")
        self.assertEqual(script.status_code, 200)
        script.close()

    def test_status(self):
        data = self.get("/api/status").json
        self.assertEqual([s["id"] for s in data["sources"]], ["youtube", "jamendo", "archive"])
        self.assertFalse(data["sources"][0]["available"])
        self.assertIn("ffmpeg", data["tools"])

    def test_search(self):
        self.assertEqual(self.get("/api/search?source=archive&q=").status_code, 400)
        self.assertEqual(self.get("/api/search?source=nope&q=x").status_code, 404)
        off = self.get("/api/search?source=youtube&q=x").json
        self.assertTrue(off["unavailable"])
        broken = self.get("/api/search?source=jamendo&q=x")
        self.assertEqual((broken.status_code, broken.json["error"]), (502, "Jamendo is down"))
        ok = self.get("/api/search?source=archive&q=dub").json
        track = ok["tracks"][0]
        self.assertEqual((track["title"], track["uid"], track["in_crate"]), ("dub one", "archive:rel/01", False))
        self.assertNotIn("downloads", track)  # download URLs stay on the server

    def test_download_flow_and_library(self):
        self.assertEqual(self.post("/api/downloads", {"uid": "archive:rel/01"}).status_code, 404)  # not searched yet
        self.get("/api/search?source=archive&q=dub")
        started = self.post("/api/downloads", {"uid": "archive:rel/01"})
        self.assertEqual(started.status_code, 202)
        self.wait(lambda: self.get("/api/downloads").json["jobs"][-1]["state"] == "done")
        tracks = self.get("/api/library").json["tracks"]
        self.assertEqual(len(tracks), 1)
        track_id = tracks[0]["id"]
        self.assertTrue(self.get("/api/search?source=archive&q=dub").json["tracks"][0]["in_crate"])
        self.wait(lambda: self.get(f"/api/library/{track_id}").json["analysis_status"] == "done")
        full = self.get(f"/api/library/{track_id}").json
        self.assertAlmostEqual(full["bpm"], 122.0, delta=0.3)
        self.assertEqual(len(full["waveform"]), 240)

        audio = self.client.get(f"/api/library/{track_id}/audio?t={TOKEN}", headers={"Range": "bytes=0-99"})
        self.assertEqual((audio.status_code, len(audio.data), audio.mimetype), (206, 100, "audio/mpeg"))
        audio.close()

        self.assertEqual(self.post(f"/api/library/{track_id}/bpm", {"factor": 2}).json["bpm"], 244.0)
        self.assertEqual(self.post(f"/api/library/{track_id}/bpm", {"factor": 3}).status_code, 400)
        self.assertEqual(self.post(f"/api/library/{track_id}/reveal").status_code, 200)
        self.assertEqual(self.revealed[-1], Path(full["path"]))

        path = Path(full["path"])
        self.assertTrue(path.exists())
        self.assertEqual(self.client.delete(f"/api/library/{track_id}", headers=self.headers).status_code, 200)
        self.assertFalse(path.exists())
        self.assertEqual(self.get("/api/library").json["tracks"], [])
        self.assertEqual(self.get(f"/api/library/{track_id}").status_code, 404)

    def test_rescan(self):
        lib_dir = Path(self.store.get().library_dir)
        make_audio_file(lib_dir / "Mira - Ferry Song.mp3", find_ffmpeg(), seconds=12)
        result = self.post("/api/library/rescan").json
        self.assertEqual((result["added"], result["removed"]), (1, 0))
        self.assertEqual(self.get("/api/library").json["tracks"][0]["artist"], "Mira")

    def test_settings(self):
        data = self.get("/api/settings").json
        self.assertIn("", data["cookie_browsers"])
        self.assertEqual(self.post("/api/settings", {"auto_analyze": "yes"}).status_code, 400)
        self.assertEqual(self.post("/api/settings", {"youtube_cookies_browser": "netscape"}).status_code, 400)
        saved = self.post("/api/settings", {"jamendo_client_id": "  abc123 ", "prefer_lossless": True}).json["settings"]
        self.assertEqual((saved["jamendo_client_id"], saved["prefer_lossless"]), ("abc123", True))
        self.assertEqual(SettingsStore(self.store.path).get().jamendo_client_id, "abc123")  # persisted


if __name__ == "__main__":
    unittest.main()
