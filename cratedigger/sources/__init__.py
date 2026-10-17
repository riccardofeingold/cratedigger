"""Music sources: YouTube (yt-dlp), Jamendo and Internet Archive netlabels."""

from __future__ import annotations

import requests

from .archive import ArchiveSource
from .base import Cancelled, DownloadResult, Source, SourceError
from .jamendo import JamendoSource
from .youtube import YouTubeSource

SOURCE_ORDER = ("youtube", "jamendo", "archive")

__all__ = ["ArchiveSource", "Cancelled", "DownloadResult", "JamendoSource", "SOURCE_ORDER",
           "Source", "SourceError", "YouTubeSource", "build_sources"]


def build_sources(settings_store, session: requests.Session) -> dict[str, Source]:
    return {
        "youtube": YouTubeSource(settings_store),
        "jamendo": JamendoSource(settings_store, session),
        "archive": ArchiveSource(session),
    }
