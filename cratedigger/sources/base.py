"""Pieces shared by the music sources."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import requests

from ..audiofile import LOSSLESS_EXTS, sniff_audio_ext
from ..models import Track

# progress(done_bytes, total_bytes, stage=None); total is 0 when unknown.
ProgressFn = Callable[..., None]


class SourceError(Exception):
    """A problem worth showing to the user as-is."""


class Cancelled(Exception):
    pass


@dataclass
class DownloadResult:
    path: Path
    tags: dict = field(default_factory=dict)  # metadata learned while downloading
    cover_url: str = ""


class Source:
    id = ""
    name = ""

    def status(self) -> tuple[bool, str]:
        """(usable, message to show when it isn't)."""
        return True, ""

    def search(self, query: str, *, cc_only: bool = False) -> list[Track]:
        raise NotImplementedError

    def download(self, track: Track, workdir: Path, progress: ProgressFn,
                 cancel: threading.Event, settings) -> DownloadResult:
        raise NotImplementedError


def network_error_message(service: str, error: Exception) -> str:
    if isinstance(error, requests.Timeout):
        return f"{service} took too long to answer. Try again in a moment."
    if isinstance(error, requests.ConnectionError):
        return f"Couldn't reach {service}. Check your internet connection."
    return f"{service} request failed: {error}"


def order_downloads(downloads: list[dict], prefer_lossless: bool) -> list[dict]:
    lossless = [d for d in downloads if d.get("lossless")]
    lossy = [d for d in downloads if not d.get("lossless")]
    return lossless + lossy if prefer_lossless else lossy + lossless


def http_download(session: requests.Session, url: str, dest: Path, progress: ProgressFn,
                  cancel: threading.Event, chunk_size: int = 256 * 1024) -> None:
    done = 0
    with session.get(url, stream=True, timeout=(10, 60)) as r:
        if r.status_code >= 400:
            raise SourceError(f"The server answered {r.status_code} for this file.")
        content_type = r.headers.get("Content-Type", "").split(";")[0].strip().lower()
        if content_type in ("text/html", "application/json", "text/plain"):
            raise SourceError("The server sent a web page instead of audio.")
        total = int(r.headers.get("Content-Length") or 0)
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size):
                if cancel.is_set():
                    raise Cancelled()
                if chunk:
                    f.write(chunk)
                    done += len(chunk)
                    progress(done, total)
    if done == 0:
        raise SourceError("The download was empty.")


def download_candidates(session: requests.Session, candidates: list[dict], workdir: Path,
                        progress: ProgressFn, cancel: threading.Event, service: str) -> Path:
    """Try each {"ext", "url", "lossless"} in turn; return the first file that is real audio."""
    last_error: str | None = None
    for i, cand in enumerate(candidates):
        dest = workdir / f"download-{i}{cand.get('ext') or '.bin'}"
        try:
            http_download(session, cand["url"], dest, progress, cancel)
        except Cancelled:
            raise
        except SourceError as e:
            last_error = str(e)
        except requests.RequestException as e:
            last_error = network_error_message(service, e)
        except OSError as e:
            raise SourceError(f"Couldn't write the download: {e.strerror or e}") from e
        else:
            real = sniff_audio_ext(dest)
            if real and not (cand.get("lossless") and real not in LOSSLESS_EXTS):
                return dest if real == dest.suffix.lower() else dest.rename(dest.with_suffix(real))
            last_error = "The file wasn't the audio format it claimed to be."
        dest.unlink(missing_ok=True)
    raise SourceError(last_error or "No downloadable file was found for this track.")
