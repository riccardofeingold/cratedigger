"""Local web server: a JSON API plus the single-page UI. Listens on 127.0.0.1 only.

Every /api call must carry the per-run token that is embedded in the page, and the
Host header must be a loopback name, so other websites open in your browser can't
drive the app.
"""

from __future__ import annotations

import hmac
import importlib.util
import logging
import os
import subprocess
import sys
import threading
from collections import OrderedDict
from dataclasses import asdict
from pathlib import Path

from flask import Flask, Response, abort, request, send_file
from werkzeug.exceptions import HTTPException

from . import __version__
from .audiofile import MIME_TYPES
from .config import COOKIE_BROWSERS
from .ffmpeg import find_ffmpeg
from .services import ACTIVE_STATES
from .sources import SOURCE_ORDER, SourceError
from .sources.youtube import has_js_runtime, ytdlp_version
from .tagging import write_tags

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "[::1]"}
CSP = ("default-src 'self'; img-src 'self' https: data:; media-src 'self' https:; "
       "frame-src https://www.youtube-nocookie.com; script-src 'self'; style-src 'self' 'unsafe-inline'; "
       "font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'")


class ResultCache:
    """Recent search results, so a download request only needs the track's uid."""

    def __init__(self, size: int = 5000):
        self._data: OrderedDict = OrderedDict()
        self._lock = threading.Lock()
        self._size = size

    def put(self, tracks) -> None:
        with self._lock:
            for t in tracks:
                self._data[t.uid] = t
                self._data.move_to_end(t.uid)
            while len(self._data) > self._size:
                self._data.popitem(last=False)

    def get(self, uid: str):
        with self._lock:
            return self._data.get(uid)


def reveal_in_file_manager(path: Path) -> None:
    if sys.platform == "win32":
        subprocess.Popen(f'explorer /select,"{path}"')
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path.parent)])


def open_folder(path: Path) -> None:
    if sys.platform == "win32":
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def _host_name(host: str) -> str:
    if host.startswith("["):
        return host[: host.find("]") + 1]
    return host.split(":")[0]


def create_app(*, settings_store, library, sources: dict, jobs, analysis, token: str,
               file_actions: dict | None = None) -> Flask:
    app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="/static")
    app.json.sort_keys = False
    cache = ResultCache()
    actions = {"reveal": reveal_in_file_manager, "open": open_folder, **(file_actions or {})}

    @app.before_request
    def guard():
        if _host_name(request.host or "") not in LOOPBACK_HOSTS:
            abort(403)  # blocks DNS-rebinding attacks
        if request.path.startswith("/api/"):
            sent = request.headers.get("X-Crate-Token") or request.args.get("t") or ""
            if not hmac.compare_digest(sent, token):
                return {"error": "This page belongs to an earlier session. Reload it."}, 403
        return None

    @app.after_request
    def headers(response: Response):
        response.headers.setdefault("Content-Security-Policy", CSP)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        return response

    @app.errorhandler(HTTPException)
    def http_error(e: HTTPException):
        if request.path.startswith("/api/"):
            return {"error": e.description or e.name}, e.code
        return e

    # ------------------------------------------------------------------ pages

    @app.get("/")
    def index():
        html = (STATIC_DIR / "index.html").read_text("utf-8")
        html = html.replace("__CRATE_TOKEN__", token).replace("__VERSION__", __version__)
        return Response(html, mimetype="text/html", headers={"Cache-Control": "no-store"})

    @app.get("/health")
    def health():
        return {"app": "cratedigger", "version": __version__}

    # ----------------------------------------------------------------- status

    @app.get("/api/status")
    def status():
        listed = []
        for source_id in SOURCE_ORDER:
            src = sources.get(source_id)
            if src is not None:
                ok, message = src.status()
                listed.append({"id": source_id, "name": src.name, "available": ok, "message": message})
        return {
            "version": __version__,
            "sources": listed,
            "library_dir": str(settings_store.get().library_path),
            "tools": {
                "ffmpeg": bool(find_ffmpeg()),
                "yt_dlp": ytdlp_version(),
                "deno": has_js_runtime(),
                "numpy": importlib.util.find_spec("numpy") is not None,
                "mutagen": importlib.util.find_spec("mutagen") is not None,
            },
        }

    # ----------------------------------------------------------------- search

    @app.get("/api/search")
    def search():
        src = sources.get(request.args.get("source", ""))
        if src is None:
            abort(404, "Unknown source")
        query = (request.args.get("q") or "").strip()[:200]
        if not query:
            return {"tracks": [], "error": "Type something to search for."}, 400
        ok, message = src.status()
        if not ok:
            return {"tracks": [], "error": message, "unavailable": True}
        try:
            tracks = src.search(query, cc_only=request.args.get("cc") == "1")
        except SourceError as e:
            return {"tracks": [], "error": str(e)}, 502
        except Exception as e:
            log.exception("%s search failed", src.name)
            return {"tracks": [], "error": f"{src.name} search failed: {e}"}, 502
        cache.put(tracks)
        in_crate = library.known_uids()
        return {"tracks": [{**t.to_public(), "in_crate": t.uid in in_crate} for t in tracks]}

    # -------------------------------------------------------------- downloads

    @app.post("/api/downloads")
    def start_download():
        data = request.get_json(silent=True) or {}
        track = cache.get(str(data.get("uid", "")))
        if track is None:
            return {"error": "That search result has expired. Search again, then download."}, 404
        return jobs.submit(track).public(), 202

    @app.get("/api/downloads")
    def list_downloads():
        listed = jobs.list()
        return {"jobs": listed, "active": sum(j["state"] in ACTIVE_STATES for j in listed)}

    @app.post("/api/downloads/<job_id>/cancel")
    def cancel_download(job_id: str):
        return {"ok": jobs.cancel(job_id)}

    @app.post("/api/downloads/clear")
    def clear_downloads():
        jobs.clear_finished()
        return {"ok": True}

    # ---------------------------------------------------------------- library

    def track_or_404(track_id: int) -> dict:
        track = library.get(track_id)
        if track is None:
            abort(404, "That track isn't in your crate anymore.")
        return track

    @app.get("/api/library")
    def list_library():
        return {"tracks": library.list_tracks()}

    @app.get("/api/library/<int:track_id>")
    def get_track(track_id: int):
        return track_or_404(track_id)

    @app.get("/api/library/<int:track_id>/audio")
    def track_audio(track_id: int):
        path = Path(track_or_404(track_id)["path"])
        if not path.is_file():
            abort(404, "The file is missing. Rescan your folder.")
        mimetype = MIME_TYPES.get(path.suffix.lower(), "application/octet-stream")
        return send_file(path, mimetype=mimetype, conditional=True, max_age=0)

    @app.post("/api/library/<int:track_id>/bpm")
    def change_bpm(track_id: int):
        track = track_or_404(track_id)
        data = request.get_json(silent=True) or {}
        factor = data.get("factor")
        if factor not in (2, 0.5):
            abort(400, "factor must be 2 or 0.5")
        if not track.get("bpm"):
            abort(400, "This track has no BPM yet.")
        bpm = round(track["bpm"] * factor, 1)
        library.update(track_id, bpm=bpm)
        try:
            write_tags(Path(track["path"]), {"bpm": bpm})
        except Exception as e:
            log.warning("Couldn't write BPM tag: %s", e)
        return {"bpm": bpm}

    @app.post("/api/library/<int:track_id>/analyze")
    def reanalyze(track_id: int):
        track_or_404(track_id)
        analysis.enqueue(track_id, overwrite=True)
        return {"ok": True}

    @app.post("/api/library/<int:track_id>/reveal")
    def reveal(track_id: int):
        path = Path(track_or_404(track_id)["path"])
        if not path.exists():
            abort(404, "The file is missing. Rescan your folder.")
        try:
            actions["reveal"](path)
        except OSError as e:
            abort(500, f"Couldn't open your file manager: {e}")
        return {"ok": True}

    @app.delete("/api/library/<int:track_id>")
    def delete_track(track_id: int):
        path = Path(track_or_404(track_id)["path"])
        try:
            path.unlink(missing_ok=True)
        except OSError as e:
            abort(500, f"Couldn't delete the file: {e.strerror or e}")
        library.delete(track_id)
        return {"ok": True}

    @app.post("/api/library/rescan")
    def rescan():
        folder = settings_store.get().library_path
        result = library.rescan(folder)
        for track_id in result["to_analyze"]:
            analysis.enqueue(track_id, overwrite=False)
        return {"added": result["added"], "removed": result["removed"]}

    @app.post("/api/library/open")
    def open_library():
        folder = settings_store.get().library_path
        folder.mkdir(parents=True, exist_ok=True)
        try:
            actions["open"](folder)
        except OSError as e:
            abort(500, f"Couldn't open the folder: {e}")
        return {"ok": True, "path": str(folder)}

    # --------------------------------------------------------------- settings

    @app.get("/api/settings")
    def get_settings():
        return {"settings": asdict(settings_store.get()), "cookie_browsers": list(COOKIE_BROWSERS)}

    @app.post("/api/settings")
    def save_settings():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            abort(400, "Send the settings as a JSON object.")
        try:
            saved = settings_store.update(data)
        except ValueError as e:
            return {"error": str(e)}, 400
        return {"settings": asdict(saved)}

    return app
