"""Synthetic audio for tests: drums at a known tempo over chords in a known key."""

from __future__ import annotations

import subprocess
import wave
from pathlib import Path

import numpy as np

SR = 22050
CODEC_ARGS = {
    ".mp3": ["-c:a", "libmp3lame", "-b:a", "192k"],
    ".flac": ["-c:a", "flac"],
    ".ogg": ["-c:a", "libvorbis", "-q:a", "4"],
    ".m4a": ["-c:a", "aac", "-b:a", "160k"],
    ".wav": ["-c:a", "pcm_s16le"],
}


def _kick(sr: int) -> np.ndarray:
    t = np.arange(int(0.25 * sr)) / sr
    freq = 50 + 90 * np.exp(-t * 30)
    return np.sin(2 * np.pi * np.cumsum(freq) / sr) * np.exp(-t * 9)


def _hat(rng, sr: int) -> np.ndarray:
    n = int(0.05 * sr)
    noise = np.diff(rng.standard_normal(n), prepend=0.0)
    return noise * np.exp(-np.arange(n) / sr * 60) * 0.25


def synth_track(bpm: float = 124.0, seconds: float = 40.0, tonic: int = 9, minor: bool = True,
                sr: int = SR, seed: int = 0) -> np.ndarray:
    """Four-on-the-floor kick, off-beat hats and a i-VI-iv-v (or I-V-vi-IV) pad.

    tonic is a pitch class: 0 = C ... 9 = A.
    """
    rng = np.random.default_rng(seed)
    n = int(seconds * sr)
    y = np.zeros(n + sr)
    beat = 60.0 / bpm
    kick = _kick(sr)
    for b in range(int(seconds / beat)):
        start = int(b * beat * sr)
        y[start:start + kick.size] += kick
        hat = _hat(rng, sr)
        off = int((b + 0.5) * beat * sr)
        y[off:off + hat.size] += hat

    scale = [0, 2, 3, 5, 7, 8, 10] if minor else [0, 2, 4, 5, 7, 9, 11]
    progression = [0, 5, 3, 4] if minor else [0, 4, 5, 3]
    root = 48 + tonic  # C3 + offset
    t = np.arange(n) / sr
    two_bars = 8 * beat
    for i in range(int(seconds / two_bars) + 1):
        a, b = int(i * two_bars * sr), min(n, int((i + 1) * two_bars * sr))
        if a >= n:
            break
        degree = progression[i % 4]
        notes = [root + scale[(degree + k) % 7] + 12 * ((degree + k) // 7) for k in (0, 2, 4)]
        for m in notes:
            for octave, amp in ((0, 0.12), (12, 0.06)):
                f = 440.0 * 2 ** ((m + octave - 69) / 12)
                y[a:b] += amp * np.sin(2 * np.pi * f * t[a:b])
        bass = 440.0 * 2 ** ((notes[0] - 12 - 69) / 12)
        y[a:b] += 0.15 * np.sin(2 * np.pi * bass * t[a:b])
    y = y[:n]
    return (0.8 * y / np.max(np.abs(y))).astype(np.float32)


def write_wav(path: Path, y: np.ndarray, sr: int = SR) -> Path:
    pcm = (np.clip(y, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())
    return path


def make_audio_file(path: Path, ffmpeg: str, seconds: float = 20.0, bpm: float = 124.0,
                    tonic: int = 9, minor: bool = True) -> Path:
    """Write a synthetic track in the format implied by path's extension."""
    path = Path(path)
    wav = path.with_name(path.stem + "-src.wav")
    write_wav(wav, synth_track(bpm=bpm, seconds=seconds, tonic=tonic, minor=minor))
    if path.suffix.lower() == ".wav":
        wav.replace(path)
        return path
    subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-i", str(wav), *CODEC_ARGS[path.suffix.lower()], str(path)],
                   check=True)
    wav.unlink()
    return path


# Bytes with a JPEG signature; tag writers store cover art as-is, so this is enough.
FAKE_JPEG = b"\xff\xd8\xff\xe0" + bytes(range(256)) + b"\xff\xd9"
