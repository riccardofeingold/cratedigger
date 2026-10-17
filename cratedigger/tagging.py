"""Read and write DJ-friendly tags (title, artist, BPM, key, cover art) with mutagen.

MP3/WAV/AIFF get ID3v2.3 (what rekordbox, Serato and Traktor read most reliably),
FLAC/Ogg get Vorbis comments (BPM, INITIALKEY), M4A gets iTunes atoms.
"""

from __future__ import annotations

import base64
from pathlib import Path

_ID3_TEXT = {"title": "TIT2", "artist": "TPE1", "album": "TALB", "genre": "TCON", "year": "TDRC"}
_VORBIS_TEXT = {"title": "TITLE", "artist": "ARTIST", "album": "ALBUM", "genre": "GENRE",
                "year": "DATE", "comment": "COMMENT", "license_url": "LICENSE"}
_MP4_TEXT = {"title": "\xa9nam", "artist": "\xa9ART", "album": "\xa9alb", "genre": "\xa9gen",
             "year": "\xa9day", "comment": "\xa9cmt"}
_MP4_KEY = "----:com.apple.iTunes:initialkey"


def cover_mime(data: bytes | None) -> str | None:
    if not data:
        return None
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    return None


def write_tags(path: Path, fields: dict, cover: bytes | None = None) -> bool:
    """Set the given fields (others stay as they are). Returns False for formats it can't tag.

    fields: title, artist, album, genre, year, comment, url, license_url, bpm, key
    """
    fields = {k: v for k, v in fields.items() if v not in (None, "")}
    mime = cover_mime(cover)
    if not mime:
        cover = None
    ext = Path(path).suffix.lower()

    if ext == ".mp3":
        from mutagen.mp3 import MP3

        audio = MP3(path)
        if audio.tags is None:
            audio.add_tags()
        _apply_id3(audio.tags, fields, cover, mime)
        audio.save(v2_version=3)
    elif ext in (".wav", ".aif", ".aiff"):
        from mutagen.aiff import AIFF
        from mutagen.wave import WAVE

        audio = WAVE(path) if ext == ".wav" else AIFF(path)
        if audio.tags is None:
            audio.add_tags()
        _apply_id3(audio.tags, fields, cover, mime)
        audio.save(v2_version=3)
    elif ext == ".flac":
        from mutagen.flac import FLAC

        audio = FLAC(path)
        if audio.tags is None:
            audio.add_tags()
        _apply_vorbis(audio, fields)
        if cover:
            audio.clear_pictures()
            audio.add_picture(_picture(cover, mime))
        audio.save()
    elif ext in (".ogg", ".opus"):
        from mutagen import File

        audio = File(path)  # OggVorbis or OggOpus
        if audio is None:
            return False
        if audio.tags is None:
            audio.add_tags()
        _apply_vorbis(audio, fields)
        if cover:
            block = base64.b64encode(_picture(cover, mime).write()).decode("ascii")
            audio["METADATA_BLOCK_PICTURE"] = [block]
        audio.save()
    elif ext == ".m4a":
        from mutagen.mp4 import MP4

        audio = MP4(path)
        if audio.tags is None:
            audio.add_tags()
        _apply_mp4(audio.tags, fields, cover, mime)
        audio.save()
    else:
        return False
    return True


def _bpm_text(value) -> str:
    # ID3 defines TBPM as an integer; DJ software re-reads the exact value anyway.
    return str(int(round(float(value))))


def _apply_id3(tags, f: dict, cover: bytes | None, mime: str | None) -> None:
    from mutagen import id3

    for key, frame_id in _ID3_TEXT.items():
        if key in f:
            frame_cls = getattr(id3, frame_id)
            tags.setall(frame_id, [frame_cls(encoding=3, text=str(f[key]))])
    if "comment" in f:
        tags.setall("COMM::eng", [id3.COMM(encoding=3, lang="eng", desc="", text=str(f["comment"]))])
    if "url" in f:
        tags.setall("WOAS", [id3.WOAS(url=f["url"])])
    if "license_url" in f:
        tags.setall("WCOP", [id3.WCOP(url=f["license_url"])])
    if "bpm" in f:
        tags.setall("TBPM", [id3.TBPM(encoding=3, text=_bpm_text(f["bpm"]))])
    if "key" in f:
        tags.setall("TKEY", [id3.TKEY(encoding=3, text=str(f["key"]))])
    if cover:
        tags.delall("APIC")
        tags.add(id3.APIC(encoding=3, mime=mime, type=3, desc="Cover", data=cover))


def _apply_vorbis(audio, f: dict) -> None:
    for key, name in _VORBIS_TEXT.items():
        if key in f:
            audio[name] = [str(f[key])]
    if "bpm" in f:
        audio["BPM"] = [_bpm_text(f["bpm"])]
    if "key" in f:
        audio["INITIALKEY"] = [str(f["key"])]
        audio["KEY"] = [str(f["key"])]


def _apply_mp4(tags, f: dict, cover: bytes | None, mime: str | None) -> None:
    from mutagen.mp4 import MP4Cover, MP4FreeForm

    for key, atom in _MP4_TEXT.items():
        if key in f:
            tags[atom] = [str(f[key])]
    if "bpm" in f:
        tags["tmpo"] = [int(round(float(f["bpm"])))]
    if "key" in f:
        tags[_MP4_KEY] = [MP4FreeForm(str(f["key"]).encode("utf-8"))]
    if cover:
        fmt = MP4Cover.FORMAT_PNG if mime == "image/png" else MP4Cover.FORMAT_JPEG
        tags["covr"] = [MP4Cover(cover, imageformat=fmt)]


def _picture(data: bytes, mime: str):
    from mutagen.flac import Picture

    pic = Picture()
    pic.type = 3  # front cover
    pic.mime = mime
    pic.desc = "Cover"
    pic.data = data
    return pic


def read_tags(path: Path) -> dict:
    """Best-effort read of title/artist/album/genre/year/bpm/key/duration."""
    from mutagen import File, MutagenError

    try:
        audio = File(path)
    except (MutagenError, OSError, ValueError):
        return {}
    if audio is None:
        return {}
    out: dict = {"duration": float(getattr(audio.info, "length", 0) or 0) or None}
    tags = audio.tags
    if tags is None:
        return out

    def first(*keys):
        for k in keys:
            try:
                v = tags.get(k)
            except (KeyError, ValueError, TypeError):
                continue
            if v is None:
                continue
            if hasattr(v, "text"):  # ID3 frame
                v = v.text
            if isinstance(v, (list, tuple)):
                v = v[0] if v else None
            if isinstance(v, bytes):
                v = v.decode("utf-8", "replace")
            if v not in (None, ""):
                return str(v).strip()
        return None

    from mutagen.mp4 import MP4Tags

    if hasattr(tags, "getall"):  # ID3
        values = dict(title=first("TIT2"), artist=first("TPE1"), album=first("TALB"),
                      genre=first("TCON"), year=first("TDRC", "TYER"), bpm=first("TBPM"),
                      key=first("TKEY"))
    elif isinstance(tags, MP4Tags):
        values = dict(title=first("\xa9nam"), artist=first("\xa9ART"), album=first("\xa9alb"),
                      genre=first("\xa9gen"), year=first("\xa9day"), bpm=first("tmpo"),
                      key=first(_MP4_KEY))
    else:  # Vorbis comments
        values = dict(title=first("title"), artist=first("artist"), album=first("album"),
                      genre=first("genre"), year=first("date"), bpm=first("bpm"),
                      key=first("initialkey", "key"))
    try:
        values["bpm"] = float(values["bpm"]) if values["bpm"] else None
    except ValueError:
        values["bpm"] = None
    if values["bpm"] is not None and not 30 <= values["bpm"] <= 300:
        values["bpm"] = None
    if values["year"]:
        values["year"] = values["year"][:4]
    out.update({k: v for k, v in values.items() if v is not None})
    return out
