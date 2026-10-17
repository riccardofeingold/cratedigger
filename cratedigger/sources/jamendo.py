"""Jamendo: independent artists, Creative Commons licensed, free API key required.

API docs: https://developer.jamendo.com/v3.0/tracks
"""

from __future__ import annotations

import json
import re

import requests

from ..licenses import license_label
from ..models import Track
from .base import DownloadResult, Source, SourceError, download_candidates, network_error_message, order_downloads

API_URL = "https://api.jamendo.com/v3.0/tracks/"
SEARCH_LIMIT = 40
NO_CLIENT_ID = "Add a free Jamendo client ID in Settings to search Jamendo."


def _text(value) -> str:
    return " ".join(str(value or "").split())


def parse_waveform(raw, points: int = 240) -> list[float] | None:
    """Jamendo sends {"peaks": [0..100, ...]} as a JSON string; downsample to 0..1."""
    if not raw:
        return None
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return None
    peaks = data.get("peaks") if isinstance(data, dict) else None
    values = [float(v) for v in (peaks or []) if isinstance(v, (int, float))]
    if not values:
        return None
    n = len(values)
    if n > points:
        values = [max(values[i * n // points:(i + 1) * n // points] or [0.0]) for i in range(points)]
    top = max(values) or 1.0
    return [round(v / top, 3) for v in values]


def parse_track(raw: dict) -> Track | None:
    """One result object from /v3.0/tracks -> Track (None if it can't be downloaded)."""
    download_url = raw.get("audiodownload") or ""
    track_id = str(raw.get("id") or "")
    if not track_id or not download_url or raw.get("audiodownload_allowed") is False:
        return None
    downloads = []
    if re.search(r"/mp32/?$", download_url):  # the same endpoint also serves FLAC
        downloads.append({"ext": ".flac", "url": re.sub(r"/mp32/?$", "/flac/", download_url), "lossless": True})
    downloads.append({"ext": ".mp3", "url": download_url, "lossless": False})

    info = raw.get("musicinfo") or {}
    tags = info.get("tags") or {}
    genres = [g for g in tags.get("genres") or [] if isinstance(g, str)][:3]
    license_url = raw.get("license_ccurl") or ""
    try:
        duration = float(raw.get("duration")) or None
    except (TypeError, ValueError):
        duration = None
    return Track(
        source="jamendo",
        source_id=track_id,
        title=_text(raw.get("name")) or "Untitled",
        artist=_text(raw.get("artist_name")) or "Unknown artist",
        album=_text(raw.get("album_name")),
        duration=duration,
        genres=genres,
        license=license_label(license_url),
        license_url=license_url,
        page_url=raw.get("shareurl") or f"https://www.jamendo.com/track/{track_id}",
        preview_url=raw.get("audio") or "",
        image_url=raw.get("image") or raw.get("album_image") or "",
        year=str(raw.get("releasedate") or "")[:4],
        downloads=downloads,
        waveform=parse_waveform(raw.get("waveform")),
        extra={"vocal": info.get("vocalinstrumental") or ""},
    )


class JamendoSource(Source):
    id = "jamendo"
    name = "Jamendo"

    def __init__(self, settings_store, session: requests.Session):
        self.settings_store = settings_store
        self.session = session

    def status(self) -> tuple[bool, str]:
        if not self.settings_store.get().jamendo_client_id:
            return False, NO_CLIENT_ID
        return True, ""

    def search(self, query: str, *, cc_only: bool = False) -> list[Track]:
        client_id = self.settings_store.get().jamendo_client_id
        if not client_id:
            raise SourceError(NO_CLIENT_ID)
        params = {
            "client_id": client_id,
            "format": "json",
            "limit": SEARCH_LIMIT,
            "search": query,
            "include": "musicinfo",
            "type": "single albumtrack",
            "audioformat": "mp32",  # preview stream in good quality
            "audiodlformat": "mp32",
            "imagesize": 300,
        }
        try:
            r = self.session.get(API_URL, params=params, timeout=(6, 20))
        except requests.RequestException as e:
            raise SourceError(network_error_message("Jamendo", e)) from e
        if r.status_code in (401, 403):
            raise SourceError("Jamendo didn't accept the client ID. Check it in Settings.")
        if r.status_code >= 400:
            raise SourceError(f"Jamendo answered {r.status_code}. Try again later.")
        try:
            data = r.json()
        except ValueError as e:
            raise SourceError("Jamendo sent a response that couldn't be read.") from e
        headers = data.get("headers") or {}
        if headers.get("status") != "success":
            message = headers.get("error_message") or "unknown error"
            if "client" in message.lower():
                raise SourceError("Jamendo didn't accept the client ID. Check it in Settings.")
            raise SourceError(f"Jamendo: {message}")
        return [t for t in (parse_track(x) for x in data.get("results") or [] if isinstance(x, dict)) if t]

    def download(self, track, workdir, progress, cancel, settings) -> DownloadResult:
        candidates = order_downloads(track.downloads, settings.prefer_lossless)
        path = download_candidates(self.session, candidates, workdir, progress, cancel, "Jamendo")
        return DownloadResult(path=path, cover_url=track.image_url)
