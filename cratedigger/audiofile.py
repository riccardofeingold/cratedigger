"""Recognise audio files by extension and by their first bytes."""

from __future__ import annotations

from pathlib import Path

AUDIO_EXTS = {".mp3", ".flac", ".ogg", ".opus", ".wav", ".aif", ".aiff", ".m4a"}
LOSSLESS_EXTS = {".flac", ".wav", ".aif", ".aiff"}

MIME_TYPES = {
    ".mp3": "audio/mpeg",
    ".flac": "audio/flac",
    ".ogg": "audio/ogg",
    ".opus": "audio/ogg",
    ".wav": "audio/wav",
    ".aif": "audio/aiff",
    ".aiff": "audio/aiff",
    ".m4a": "audio/mp4",
}


def is_audio_path(path: Path) -> bool:
    return path.suffix.lower() in AUDIO_EXTS


def sniff_audio_ext(path: Path) -> str | None:
    """Return the real extension of an audio file from its header, or None."""
    try:
        with open(path, "rb") as f:
            head = f.read(64)
    except OSError:
        return None
    if len(head) < 12:
        return None
    if head[:4] == b"fLaC":
        return ".flac"
    if head[:4] == b"OggS":
        return ".opus" if b"OpusHead" in head else ".ogg"
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return ".wav"
    if head[:4] == b"FORM" and head[8:12] in (b"AIFF", b"AIFC"):
        return ".aiff"
    if head[4:8] == b"ftyp":
        return ".m4a"
    if head[:3] == b"ID3":
        return ".mp3"
    # MPEG audio frame sync; layer bits 00 would be AAC (ADTS), not MP3.
    if head[0] == 0xFF and (head[1] & 0xE0) == 0xE0 and (head[1] & 0x06) != 0:
        return ".mp3"
    return None
