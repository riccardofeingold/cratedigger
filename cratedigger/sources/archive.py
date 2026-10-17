"""Internet Archive netlabels: thousands of Creative Commons electronic releases, no key needed.

Search: https://archive.org/advancedsearch.php    Item files: https://archive.org/metadata/<id>
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import PurePosixPath
from urllib.parse import quote

import requests

from ..licenses import is_open_license, license_label
from ..models import Track
from ..naming import parse_filename
from .base import DownloadResult, Source, SourceError, download_candidates, network_error_message, order_downloads

SEARCH_URL = "https://archive.org/advancedsearch.php"
METADATA_URL = "https://archive.org/metadata/{}"
ITEMS_PER_SEARCH = 12
TRACKS_PER_ITEM = 25
MAX_TRACKS = 80

_LUCENE_SPECIAL = re.compile(r'([+\-!(){}\[\]^"~*?:\\/]|&&|\|\|)')
_MP3_QUALITY = (("320", 80), ("256", 75), ("vbr", 70), ("224", 68), ("192", 65), ("160", 55),
                ("128", 45), ("96", 30), ("64", 20))


def build_query(text: str) -> str:
    """User words (all required) limited to CC-licensed netlabel audio."""
    terms = []
    for phrase, word in re.findall(r'"([^"]+)"|(\S+)', text):
        if phrase:
            terms.append('"' + phrase.replace("\\", "\\\\").replace('"', '\\"') + '"')
        elif word:
            if word in ("AND", "OR", "NOT"):
                word = word.lower()
            terms.append(_LUCENE_SPECIAL.sub(r"\\\1", word))
    if not terms:
        raise SourceError("Type something to search for.")
    return f"({' AND '.join(terms)}) AND collection:(netlabels) AND mediatype:(audio)"


def _first(value):
    if isinstance(value, list):
        return value[0] if value else None
    return value


def _clean(value) -> str:
    return " ".join(str(value or "").split())


def _rank(name: str, fmt: str | None, source: str | None) -> tuple[bool, int] | None:
    """(lossless, quality score) for audio files; None for everything else."""
    ext = PurePosixPath(name).suffix.lower()
    bonus = 5 if source == "original" else 0
    if ext == ".flac":
        return True, 100 + bonus
    if ext in (".wav", ".aif", ".aiff"):
        return True, 90 + bonus
    if ext == ".mp3":
        f = (fmt or "").lower()
        for marker, score in _MP3_QUALITY:
            if marker in f:
                return False, score + bonus
        return False, 60 + bonus
    if ext == ".ogg":
        return False, 50 + bonus
    return None


def _parse_length(value) -> float | None:
    """IA lengths come as "291.6" or "04:51" or "1:02:03"."""
    text = str(_first(value) or "").strip()
    if not text:
        return None
    try:
        if ":" in text:
            seconds = 0.0
            for part in text.split(":"):
                seconds = seconds * 60 + float(part)
            return seconds or None
        return float(text) or None
    except ValueError:
        return None


def _track_number(value) -> int:
    m = re.match(r"\s*(\d+)", str(_first(value) or ""))
    return int(m.group(1)) if m else 9999


def _subjects(value, exclude: set[str]) -> list[str]:
    raw = value if isinstance(value, list) else re.split(r"[;,]", str(value or ""))
    out: list[str] = []
    for s in raw:
        s = _clean(s).lower()
        if s and s not in exclude and "netlabel" not in s and s not in out and len(s) <= 24:
            out.append(s)
    return out[:3]


def _file_url(identifier: str, name: str) -> str:
    return f"https://archive.org/download/{quote(identifier, safe='')}/{quote(name, safe='/')}"


def parse_item(meta: dict, doc: dict | None = None) -> list[Track]:
    """Metadata API response for one release -> one Track per song (formats merged)."""
    md = meta.get("metadata") or {}
    doc = doc or {}
    identifier = _first(md.get("identifier")) or doc.get("identifier")
    license_url = _first(md.get("licenseurl")) or _first(doc.get("licenseurl")) or ""
    if not identifier or not is_open_license(license_url):
        return []
    release = _clean(_first(md.get("title"))) or identifier
    creator = md.get("creator")
    item_artist = _clean(", ".join(creator) if isinstance(creator, list) else creator)
    year = str(_first(md.get("year")) or _first(md.get("date")) or "")[:4]
    exclude = {item_artist.lower(), release.lower()}
    genres = _subjects(md.get("subject"), exclude)

    files = [f for f in meta.get("files") or [] if isinstance(f, dict) and isinstance(f.get("name"), str)]
    by_name = {f["name"]: f for f in files}
    groups: dict[str, list[dict]] = {}
    for f in files:
        if _rank(f["name"], f.get("format"), f.get("source")) is None:
            continue
        root = f["name"]
        original = f.get("original")
        if (f.get("source") == "derivative" and isinstance(original, str) and original in by_name
                and _rank(original, by_name[original].get("format"), "original")):
            root = original  # e.g. the MP3 IA made from an uploaded FLAC
        groups.setdefault(str(PurePosixPath(root).with_suffix("")), []).append(f)

    ordered = []
    for stem, members in groups.items():
        primary = next((f for f in members if f.get("source") == "original"), members[0])
        candidates = [primary, *members]

        def pick(field: str) -> str:
            return next((_clean(_first(f.get(field))) for f in candidates if _clean(_first(f.get(field)))), "")

        title = pick("title") or parse_filename(PurePosixPath(stem).name)[1]
        artist = pick("artist") or pick("creator") or item_artist or "Unknown artist"
        downloads = []
        for f in members:
            lossless, quality = _rank(f["name"], f.get("format"), f.get("source"))
            downloads.append({"ext": PurePosixPath(f["name"]).suffix.lower(), "url": _file_url(identifier, f["name"]),
                              "lossless": lossless, "quality": quality})
        downloads.sort(key=lambda d: -d["quality"])
        preview = next((d["url"] for d in downloads if d["ext"] == ".mp3"), "") or downloads[0]["url"]
        duration = next((d for d in (_parse_length(f.get("length")) for f in candidates) if d), None)
        track = Track(
            source="archive",
            source_id=f"{identifier}/{stem}",
            title=title,
            artist=artist,
            album=pick("album") or release,
            duration=duration,
            genres=genres,
            license=license_label(license_url),
            license_url=license_url,
            page_url=f"https://archive.org/details/{quote(identifier, safe='')}",
            preview_url=preview,
            image_url=f"https://archive.org/services/img/{quote(identifier, safe='')}",
            year=year,
            downloads=downloads,
        )
        ordered.append((_track_number(primary.get("track")), stem, track))
    ordered.sort(key=lambda t: (t[0], t[1]))
    return [t for _, _, t in ordered][:TRACKS_PER_ITEM]


class ArchiveSource(Source):
    id = "archive"
    name = "Internet Archive"

    def __init__(self, session: requests.Session):
        self.session = session

    def _metadata(self, doc: dict) -> list[Track]:
        try:
            r = self.session.get(METADATA_URL.format(quote(doc["identifier"], safe="")), timeout=(6, 20))
            if r.ok:
                return parse_item(r.json(), doc)
        except (requests.RequestException, ValueError):
            pass
        return []

    def search(self, query: str, *, cc_only: bool = False) -> list[Track]:
        params = {"q": build_query(query), "fl[]": "identifier,title,creator,licenseurl",
                  "rows": ITEMS_PER_SEARCH, "page": 1, "output": "json"}
        try:
            r = self.session.get(SEARCH_URL, params=params, timeout=(6, 25))
        except requests.RequestException as e:
            raise SourceError(network_error_message("the Internet Archive", e)) from e
        if r.status_code >= 400:
            raise SourceError(f"The Internet Archive answered {r.status_code}. Try again later.")
        try:
            docs = r.json()["response"]["docs"]
        except (ValueError, KeyError, TypeError) as e:
            raise SourceError("The Internet Archive sent a response that couldn't be read.") from e
        docs = [d for d in docs if isinstance(d, dict) and d.get("identifier")
                and is_open_license(_first(d.get("licenseurl")))]
        with ThreadPoolExecutor(max_workers=6) as pool:
            items = list(pool.map(self._metadata, docs))
        return [t for item in items for t in item][:MAX_TRACKS]

    def download(self, track, workdir, progress, cancel, settings) -> DownloadResult:
        candidates = order_downloads(track.downloads, settings.prefer_lossless)
        path = download_candidates(self.session, candidates, workdir, progress, cancel, "the Internet Archive")
        return DownloadResult(path=path, cover_url=track.image_url)
