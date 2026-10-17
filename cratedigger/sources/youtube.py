"""YouTube search and audio download through yt-dlp.

Downloads take the best audio stream and convert it to 320 kbps MP3, the format every
DJ application reads. yt-dlp needs a JavaScript runtime (Deno) for reliable YouTube
downloads, and ffmpeg for the conversion.
"""

from __future__ import annotations

import logging
import re
import shutil
import threading
from pathlib import Path
from urllib.parse import quote_plus

from ..ffmpeg import find_ffmpeg
from ..models import Track
from ..naming import clean_channel_name, split_artist_title
from .base import Cancelled, DownloadResult, ProgressFn, Source, SourceError

log = logging.getLogger(__name__)

SEARCH_LIMIT = 25
CC_SEARCH_FILTER = "EgIwAQ%253D%253D"  # YouTube's own "Creative Commons" filter value
CC_BY_URL = "https://creativecommons.org/licenses/by/3.0/"
INSTALL_HINT = 'Install yt-dlp to search YouTube: pip install -U "yt-dlp[default]"'
_VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")


def ytdlp_version() -> str | None:
    try:
        from yt_dlp.version import __version__
    except Exception:
        return None
    return __version__


def has_js_runtime() -> bool:
    """yt-dlp only enables Deno by default; other runtimes need opting in."""
    return shutil.which("deno") is not None


class _QuietLogger:
    def debug(self, msg):  # yt-dlp routes info messages here too
        pass

    def info(self, msg):
        pass

    def warning(self, msg):
        log.debug("yt-dlp warning: %s", msg)

    def error(self, msg):
        log.debug("yt-dlp error: %s", msg)


def friendly_error(message: str) -> str:
    """Turn yt-dlp's error text into something a person can act on."""
    text = re.sub(r"^(ERROR:\s*)+", "", str(message)).strip()
    low = text.lower()
    if "sign in to confirm" in low or "not a bot" in low:
        return ("YouTube wants a sign-in check. In Settings, choose the browser you're signed in to "
                "YouTube with, then try again.")
    if "confirm your age" in low or "age-restricted" in low or "inappropriate for some users" in low:
        return "This video is age-restricted. In Settings, choose a browser where you're signed in to YouTube."
    if "private video" in low:
        return "This video is private."
    if "requested format is not available" in low or "javascript runtime" in low or "challenge" in low:
        return ("yt-dlp couldn't get this video's audio. Update it with pip install -U \"yt-dlp[default]\" "
                "and install Deno from deno.com.")
    if "ffmpeg" in low or "ffprobe" in low:
        return "ffmpeg is needed to convert the audio. Run: pip install imageio-ffmpeg"
    if "429" in low or "too many requests" in low:
        return "YouTube is limiting requests right now. Wait a few minutes and try again."
    if any(s in low for s in _NETWORK_HINTS):
        return "Couldn't reach YouTube. Check your internet connection."
    if "video unavailable" in low or "not available" in low:
        return "This video isn't available."
    first = text.splitlines()[0] if text else ""
    first = re.sub(r"^\[[^\]]+\]\s*[\w-]+:\s*", "", first)  # drop "[youtube] <id>: "
    first = re.split(r";\s*please report this issue|\s*\(caused by", first, flags=re.IGNORECASE)[0]
    return first.strip()[:300] or "The YouTube download failed."


_NETWORK_HINTS = ("unable to download api page", "unable to download webpage", "unable to connect",
                  "connection refused", "connection reset", "timed out", "name or service not known",
                  "nodename nor servname", "getaddrinfo failed", "temporary failure in name resolution",
                  "network is unreachable", "tunnel connection failed", "failed to resolve")


def parse_entry(entry: dict, cc_only: bool = False) -> Track | None:
    """One flat search entry -> Track. Skips channels, playlists, shorts and live streams."""
    if not isinstance(entry, dict):
        return None
    video_id = entry.get("id") or ""
    if not _VIDEO_ID.fullmatch(video_id):
        return None
    if (entry.get("ie_key") or "Youtube") != "Youtube" or "/shorts/" in (entry.get("url") or ""):
        return None
    if entry.get("live_status") in ("is_live", "is_upcoming", "post_live"):
        return None
    duration = entry.get("duration")
    if isinstance(duration, (int, float)) and duration < 30:
        return None
    video_title = entry.get("title") or ""
    channel = entry.get("channel") or entry.get("uploader") or ""
    artist, title = split_artist_title(video_title, channel)
    return Track(
        source="youtube",
        source_id=video_id,
        title=title,
        artist=artist,
        duration=float(duration) if isinstance(duration, (int, float)) else None,
        license="CC BY" if cc_only else "YouTube",
        license_url=CC_BY_URL if cc_only else "",
        page_url=f"https://www.youtube.com/watch?v={video_id}",
        image_url=f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg",
        extra={"channel": clean_channel_name(channel), "views": entry.get("view_count"),
               "video_title": video_title},
    )


def tags_from_info(info: dict, track: Track | None = None) -> dict:
    """Prefer YouTube's music metadata (artist/track/album) over parsing the video title."""
    artists = info.get("artists")
    artist = ", ".join(artists) if isinstance(artists, list) and artists else info.get("artist")
    title = info.get("track")
    if not (artist and title):
        fallback_title = info.get("title") or (track.extra.get("video_title") if track else "")
        fallback_channel = info.get("channel") or info.get("uploader") or (track.extra.get("channel") if track else "")
        artist, title = split_artist_title(fallback_title, fallback_channel)
    license_text = str(info.get("license") or "")
    cc = "creative commons" in license_text.lower()
    year = str(info.get("release_year") or "") or str(info.get("upload_date") or "")[:4]
    return {
        "title": title,
        "artist": artist,
        "album": info.get("album") or "",
        "year": year,
        "license": "CC BY" if cc else "YouTube",
        "license_url": CC_BY_URL if cc else "",
        "duration": info.get("duration"),
    }


def download_audio(url: str, workdir: Path, progress: ProgressFn, cancel: threading.Event,
                   base_options: dict) -> tuple[Path, dict]:
    """Download the best audio for `url` into workdir as MP3. Returns (path, yt-dlp info)."""
    import yt_dlp

    def on_progress(d: dict) -> None:
        if cancel.is_set():
            raise yt_dlp.utils.DownloadCancelled()
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            progress(int(d.get("downloaded_bytes") or 0), int(total))

    def on_postprocess(d: dict) -> None:
        if cancel.is_set():
            raise yt_dlp.utils.DownloadCancelled()
        if d.get("status") == "started" and d.get("postprocessor") == "ExtractAudio":
            progress(0, 0, "converting")

    options = dict(base_options)
    options.update({
        "format": "bestaudio/best",
        "outtmpl": str(workdir / "%(id)s.%(ext)s"),
        "noplaylist": True,
        "overwrites": True,
        "retries": 5,
        "fragment_retries": 5,
        "progress_hooks": [on_progress],
        "postprocessor_hooks": [on_postprocess],
        "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "320"}],
    })
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=True) or {}
    except yt_dlp.utils.DownloadCancelled as e:
        raise Cancelled() from e
    except yt_dlp.utils.DownloadError as e:
        if cancel.is_set():
            raise Cancelled() from e
        raise SourceError(friendly_error(str(e))) from e
    if cancel.is_set():
        raise Cancelled()

    path = None
    for requested in info.get("requested_downloads") or []:
        candidate = requested.get("filepath")
        if candidate and Path(candidate).exists():
            path = Path(candidate)
    if path is None:
        path = next(iter(sorted(workdir.glob("*.mp3"))), None)
    if path is None:
        raise SourceError("yt-dlp finished, but no audio file was produced.")
    return path, info


class YouTubeSource(Source):
    id = "youtube"
    name = "YouTube"

    def __init__(self, settings_store):
        self.settings_store = settings_store

    def status(self) -> tuple[bool, str]:
        if ytdlp_version() is None:
            return False, INSTALL_HINT
        return True, ""

    def options(self, *, use_cookies: bool) -> dict:
        settings = self.settings_store.get()
        opts = {"quiet": True, "no_warnings": True, "noprogress": True, "logger": _QuietLogger(),
                "socket_timeout": 20}
        if use_cookies and settings.youtube_cookies_browser:
            opts["cookiesfrombrowser"] = (settings.youtube_cookies_browser,)
        ffmpeg = find_ffmpeg()
        if ffmpeg:
            opts["ffmpeg_location"] = ffmpeg
        return opts

    def search(self, query: str, *, cc_only: bool = False) -> list[Track]:
        try:
            import yt_dlp
        except ImportError as e:
            raise SourceError(INSTALL_HINT) from e
        query = query.strip()
        if not query:
            raise SourceError("Type something to search for.")
        if cc_only:
            target = f"https://www.youtube.com/results?search_query={quote_plus(query)}&sp={CC_SEARCH_FILTER}"
        else:
            target = f"ytsearch{SEARCH_LIMIT}:{query}"
        opts = self.options(use_cookies=False)
        opts.update(extract_flat="in_playlist", skip_download=True, playlistend=SEARCH_LIMIT)
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(target, download=False) or {}
        except yt_dlp.utils.DownloadError as e:
            raise SourceError(friendly_error(str(e))) from e
        tracks = [t for t in (parse_entry(e, cc_only) for e in list(info.get("entries") or [])) if t]
        return tracks[:SEARCH_LIMIT]

    def download(self, track, workdir, progress, cancel, settings) -> DownloadResult:
        try:
            import yt_dlp  # noqa: F401
        except ImportError as e:
            raise SourceError(INSTALL_HINT) from e
        if not find_ffmpeg():
            raise SourceError("ffmpeg is needed to save YouTube audio. Run: pip install imageio-ffmpeg")
        path, info = download_audio(f"https://www.youtube.com/watch?v={track.source_id}", workdir,
                                    progress, cancel, self.options(use_cookies=True))
        return DownloadResult(path=path, tags=tags_from_info(info, track),
                              cover_url=f"https://i.ytimg.com/vi/{track.source_id}/hqdefault.jpg")
