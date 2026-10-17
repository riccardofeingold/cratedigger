"""Safe file names, and turning video titles into clean "Artist - Title" pairs."""

from __future__ import annotations

import re
import unicodedata

_ILLEGAL_CHARS = re.compile(r'[<>"|?*\x00-\x1f]')
_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

# Bits of video titles that say nothing about the music. Mix names such as
# "(Extended Mix)" or "[Original Mix]" are deliberately kept: DJs need them.
_NOISE = (
    r"(?:official\s+)?(?:music\s+|lyrics?\s+|hd\s+|4k\s+)?(?:video|audio|visuali[sz]er|clip)"
    r"(?:\s+(?:hd|hq|4k))?"
    r"|lyrics?|hd|hq|4k|official|free\s+download|out\s+now|premiere"
)
_BRACKETED_NOISE = re.compile(r"\s*[(\[]\s*(?:" + _NOISE + r")\s*[)\]]", re.IGNORECASE)
_PIPE_TAIL = re.compile(
    r"\s*[|｜].*\b(?:official|video|audio|lyrics?|visuali[sz]er|out\s+now|free\s+download)\b.*$",
    re.IGNORECASE,
)
_ARTIST_TITLE_SEP = re.compile(r"\s+[-–—~]\s+")
_LEADING_TRACK_NO = re.compile(r"^\s*\d{1,3}\s*(?:[.)_-]\s*|\s+)")


def safe_filename(name: str, max_len: int = 120) -> str:
    """Make a string safe to use as a file name on Windows, macOS and Linux."""
    s = unicodedata.normalize("NFC", name or "")
    s = s.replace("/", "-").replace("\\", "-").replace(":", " -")
    s = _ILLEGAL_CHARS.sub("", s)
    s = re.sub(r"\s+", " ", s).strip(" .")
    if len(s) > max_len:
        s = s[:max_len].rstrip(" .")
    if not s:
        s = "Untitled"
    if s.split(".")[0].upper() in _WINDOWS_RESERVED:
        s = "_" + s
    return s


def clean_channel_name(channel: str | None) -> str:
    """"Some Artist - Topic" and "SomeArtistVEVO" both become the artist's name."""
    name = (channel or "").strip()
    name = re.sub(r"\s*-\s*Topic$", "", name, flags=re.IGNORECASE)
    name = re.sub(r"\s*VEVO$", "", name)
    return name.strip()


def clean_video_title(title: str | None) -> str:
    t = (title or "").strip()
    t = _PIPE_TAIL.sub("", t)
    previous = None
    while previous != t:  # strip several noise tags, e.g. "(Official Video) [HD]"
        previous = t
        t = _BRACKETED_NOISE.sub("", t)
    return re.sub(r"\s+", " ", t).strip(" -–—")


def split_artist_title(title: str | None, fallback_artist: str | None = "") -> tuple[str, str]:
    """Split "Artist - Title (Official Video)" into ("Artist", "Title")."""
    cleaned = clean_video_title(title)
    parts = _ARTIST_TITLE_SEP.split(cleaned, maxsplit=1)
    if len(parts) == 2 and parts[0].strip() and parts[1].strip():
        return parts[0].strip(), parts[1].strip()
    artist = clean_channel_name(fallback_artist) or "Unknown artist"
    return artist, cleaned or "Untitled"


def parse_filename(stem: str) -> tuple[str, str]:
    """Guess (artist, title) from a file name like "01 - Artist - Title"."""
    s = _LEADING_TRACK_NO.sub("", stem.replace("_", " ")).strip()
    parts = _ARTIST_TITLE_SEP.split(s, maxsplit=1)
    if len(parts) == 2 and parts[0].strip() and parts[1].strip():
        return parts[0].strip(), parts[1].strip()
    return "Unknown artist", s or stem
