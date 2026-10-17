"""Background work: the download queue and the BPM/key analysis queue."""

from __future__ import annotations

import logging
import shutil
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import requests

from .audiofile import sniff_audio_ext
from .models import Track
from .naming import safe_filename
from .sources import Cancelled, SourceError
from .tagging import cover_mime, write_tags

log = logging.getLogger(__name__)

ACTIVE_STATES = {"queued", "downloading", "converting", "tagging"}


@dataclass
class Job:
    id: str
    uid: str
    title: str
    artist: str
    source: str
    state: str = "queued"  # queued|downloading|converting|tagging|done|error|cancelled
    progress: float = 0.0  # 0..1 while downloading (0 when the size is unknown)
    bytes_done: int = 0
    bytes_total: int = 0
    message: str = ""
    track_id: int | None = None
    created_at: float = field(default_factory=time.time)
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)

    def public(self) -> dict:
        keys = ("id", "uid", "title", "artist", "source", "state", "progress", "bytes_done",
                "bytes_total", "message", "track_id")
        return {k: getattr(self, k) for k in keys}


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    for i in range(2, 1000):
        candidate = path.with_name(f"{path.stem} ({i}){path.suffix}")
        if not candidate.exists():
            return candidate
    raise SourceError("Too many files with the same name in the library folder.")


class AnalysisQueue:
    """Runs BPM/key detection one track at a time and writes the results to tags."""

    def __init__(self, library):
        self.library = library
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="analysis")
        self._queued: set[int] = set()
        self._lock = threading.Lock()

    def enqueue(self, track_id: int, overwrite: bool = True) -> None:
        """overwrite=False keeps BPM/key that came from existing tags (only adds the waveform)."""
        with self._lock:
            if track_id in self._queued:
                return
            self._queued.add(track_id)
        self.library.update(track_id, analysis_status="pending", analysis_error="")
        self._pool.submit(self._run, track_id, overwrite)

    def resume(self) -> None:
        for track_id in self.library.ids_needing_analysis():
            self.enqueue(track_id, overwrite=False)

    def _fail(self, track_id: int, message: str) -> None:
        try:
            self.library.update(track_id, analysis_status="error", analysis_error=message)
        except Exception:  # the library may already be closed at shutdown
            log.debug("Couldn't record analysis error for track %s", track_id)

    def _run(self, track_id: int, overwrite: bool) -> None:
        try:
            row = self.library.get(track_id)
            if not row:
                return
            path = Path(row["path"])
            if not path.exists():
                self._fail(track_id, "The file is missing.")
                return
            self.library.update(track_id, analysis_status="running")
            try:
                from .analysis import AnalysisError, analyze_file
            except ImportError:
                self._fail(track_id, "BPM/key detection needs numpy: pip install numpy")
                return
            try:
                result = analyze_file(path)
            except AnalysisError as e:
                self._fail(track_id, str(e))
                return

            fields = {"waveform": result.get("waveform"), "analysis_status": "done", "analysis_error": ""}
            if result.get("duration"):
                fields["duration"] = result["duration"]
            keep_tagged = not overwrite and row.get("bpm") and row.get("camelot")
            if not keep_tagged:
                fields.update(bpm=result.get("bpm"), musical_key=result.get("key", ""),
                              camelot=result.get("camelot", ""))
            self.library.update(track_id, **fields)
            if not keep_tagged:
                try:
                    write_tags(path, {"bpm": result.get("bpm"), "key": result.get("key")})
                except Exception as e:  # tagging is a bonus; the crate already has the values
                    log.warning("Couldn't write BPM/key tags to %s: %s", path, e)
        except Exception as e:
            log.exception("Analysis failed for track %s", track_id)
            self._fail(track_id, f"Analysis failed: {e}")
        finally:
            with self._lock:
                self._queued.discard(track_id)

    def shutdown(self, wait: bool = False) -> None:
        self._pool.shutdown(wait=wait, cancel_futures=True)


class JobManager:
    """Downloads tracks in the background, tags them and files them into the library folder."""

    def __init__(self, library, settings_store, sources: dict, analysis: AnalysisQueue,
                 session: requests.Session, max_parallel: int = 3):
        self.library = library
        self.settings_store = settings_store
        self.sources = sources
        self.analysis = analysis
        self.session = session
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._move_lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=max_parallel, thread_name_prefix="download")

    # ----------------------------------------------------------------- public

    def submit(self, track: Track) -> Job:
        with self._lock:
            for job in self._jobs.values():
                if job.uid == track.uid and job.state in ACTIVE_STATES:
                    return job
            job = Job(id=uuid.uuid4().hex[:12], uid=track.uid, title=track.title,
                      artist=track.artist, source=track.source)
            existing = self.library.find_by_source(track.source, track.source_id)
            if existing and Path(existing["path"]).exists():
                job.state, job.progress, job.track_id = "done", 1.0, existing["id"]
                job.message = "Already in your crate"
                self._jobs[job.id] = job
                return job
            self._jobs[job.id] = job
        self._pool.submit(self._run, job, track)
        return job

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
        if not job or job.state not in ACTIVE_STATES:
            return False
        job.cancel_event.set()
        if job.state == "queued":
            job.state, job.message = "cancelled", "Cancelled"
        return True

    def clear_finished(self) -> None:
        with self._lock:
            self._jobs = {k: j for k, j in self._jobs.items() if j.state in ACTIVE_STATES}

    def list(self) -> list[dict]:
        with self._lock:
            jobs = list(self._jobs.values())[-60:]
        return [j.public() for j in jobs]

    def shutdown(self, wait: bool = False) -> None:
        with self._lock:
            for job in self._jobs.values():
                job.cancel_event.set()
        self._pool.shutdown(wait=wait, cancel_futures=True)

    # --------------------------------------------------------------- the work

    def _fetch_cover(self, url: str) -> bytes | None:
        if not url or not url.startswith(("http://", "https://")):
            return None
        try:
            r = self.session.get(url, timeout=(5, 10))
        except requests.RequestException:
            return None
        if r.ok and len(r.content) < 5_000_000 and cover_mime(r.content):
            return r.content
        return None

    def _run(self, job: Job, track: Track) -> None:
        if job.cancel_event.is_set():
            job.state, job.message = "cancelled", "Cancelled"
            return
        settings = self.settings_store.get()
        source = self.sources.get(track.source)
        workdir = Path(tempfile.mkdtemp(prefix="cratedigger-"))

        def progress(done: int, total: int, stage: str | None = None) -> None:
            if stage:
                job.state = stage
            job.bytes_done, job.bytes_total = done, total
            job.progress = min(1.0, done / total) if total else 0.0

        try:
            if source is None:
                raise SourceError(f"Unknown source: {track.source}")
            job.state = "downloading"
            result = source.download(track, workdir, progress, job.cancel_event, settings)
            if job.cancel_event.is_set():
                raise Cancelled()

            job.state = "tagging"
            path = result.path
            real_ext = sniff_audio_ext(path)
            if real_ext and real_ext != path.suffix.lower():
                path = path.rename(path.with_suffix(real_ext))
            meta = {"title": track.title, "artist": track.artist, "album": track.album,
                    "genre": ", ".join(track.genres), "year": track.year, "license": track.license,
                    "license_url": track.license_url, "page_url": track.page_url,
                    "duration": track.duration}
            meta.update({k: v for k, v in result.tags.items() if v})
            comment = " | ".join(x for x in (meta["license"], meta["page_url"]) if x)
            cover = self._fetch_cover(result.cover_url or track.image_url)
            try:
                write_tags(path, {"title": meta["title"], "artist": meta["artist"], "album": meta["album"],
                                  "genre": meta["genre"], "year": meta["year"], "comment": comment,
                                  "url": meta["page_url"], "license_url": meta["license_url"]}, cover)
            except Exception as e:
                log.warning("Couldn't tag %s: %s", path, e)

            library_dir = settings.library_path
            library_dir.mkdir(parents=True, exist_ok=True)
            name = f"{safe_filename(meta['artist'], 80)} - {safe_filename(meta['title'], 100)}{path.suffix.lower()}"
            with self._move_lock:
                dest = unique_path(library_dir / name)
                shutil.move(str(path), str(dest))
            job.track_id = self.library.add(
                dest, source=track.source, source_id=track.source_id, title=meta["title"],
                artist=meta["artist"], album=meta["album"], genre=meta["genre"], year=meta["year"],
                duration=meta["duration"], license=meta["license"], license_url=meta["license_url"],
                page_url=meta["page_url"], image_url=track.image_url,
                analysis_status="pending" if settings.auto_analyze else "skipped")
            job.state, job.progress, job.message = "done", 1.0, ""
            if settings.auto_analyze:
                self.analysis.enqueue(job.track_id)
        except Cancelled:
            job.state, job.message = "cancelled", "Cancelled"
        except SourceError as e:
            job.state, job.message = "error", str(e)
        except Exception as e:
            log.exception("Download failed for %s", track.uid)
            job.state, job.message = "error", f"Download failed: {e}"
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
