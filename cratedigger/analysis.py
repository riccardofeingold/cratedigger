"""BPM, musical key (with Camelot code) and a waveform overview for one track.

ffmpeg decodes the file; the analysis itself is plain numpy, so it installs anywhere
numpy does.

- Tempo: spectral-flux onset envelope -> autocorrelation -> harmonic comb over
  60-200 BPM with a gentle prior around 125 BPM, then a fine search using up to
  32 beat periods, which gives roughly 0.1 BPM precision on steady electronic music.
- Key: log-frequency chroma from the middle of the track, correlated against the
  Krumhansl-Kessler major/minor profiles.

Half/double-time picks (e.g. drum & bass reported as 87 instead of 174) are the
usual ambiguity; the app lets you fix those with x2 / /2.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import numpy as np

from .ffmpeg import find_ffmpeg, no_window_flags

SR = 22050
MAX_DECODE_SECONDS = 900  # 15 minutes is plenty for a single track
EXCERPT_SECONDS = 180

MAJOR_PROFILE = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
MINOR_PROFILE = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
MAJOR_NAMES = ["C", "Db", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]
MINOR_NAMES = ["Cm", "C#m", "Dm", "Ebm", "Em", "Fm", "F#m", "Gm", "G#m", "Am", "Bbm", "Bm"]


class AnalysisError(Exception):
    pass


# --------------------------------------------------------------------------- keys

def key_name(tonic: int, mode: str) -> str:
    return (MINOR_NAMES if mode == "minor" else MAJOR_NAMES)[tonic % 12]


def camelot(tonic: int, mode: str) -> str:
    """Camelot code: C major = 8B, A minor = 8A, one step clockwise = up a fifth."""
    letter = "A" if mode == "minor" else "B"
    major_tonic = (tonic + 3) % 12 if mode == "minor" else tonic % 12  # relative major
    number = ((7 * major_tonic) % 12 + 7) % 12 + 1
    return f"{number}{letter}"


_CAMELOT_TO_KEY = {camelot(t, m): key_name(t, m) for t in range(12) for m in ("major", "minor")}
_NOTE_PC = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
_KEY_TEXT = re.compile(r"([A-Ga-g])\s*([#♯b♭]?)\s*(minor|min|major|maj|m|M)?")


def parse_key(text: str | None) -> tuple[str, str] | None:
    """Read a key tag ("Am", "F#m", "Eb", "A minor", "8A", Open Key "1m") -> (key, camelot)."""
    if not text:
        return None
    s = text.strip()
    m = re.fullmatch(r"0?(\d{1,2})\s*([ABab])", s)
    if m and 1 <= int(m.group(1)) <= 12:
        code = f"{int(m.group(1))}{m.group(2).upper()}"
        return _CAMELOT_TO_KEY[code], code
    m = re.fullmatch(r"(\d{1,2})\s*([dmDM])", s)  # Traktor's Open Key: 1d = C, 1m = Am
    if m and 1 <= int(m.group(1)) <= 12:
        code = f"{(int(m.group(1)) + 6) % 12 + 1}{'A' if m.group(2).lower() == 'm' else 'B'}"
        return _CAMELOT_TO_KEY[code], code
    m = _KEY_TEXT.fullmatch(s)
    if not m:
        return None
    tonic = _NOTE_PC[m.group(1).upper()]
    if m.group(2) in ("#", "♯"):
        tonic += 1
    elif m.group(2) in ("b", "♭"):
        tonic -= 1
    mode_text = m.group(3) or ""
    mode = "minor" if mode_text in ("m", "min", "minor") else "major"
    return key_name(tonic % 12, mode), camelot(tonic % 12, mode)


# ------------------------------------------------------------------------ decoding

def decode_audio(path: Path | str, sr: int = SR, max_seconds: float = MAX_DECODE_SECONDS) -> np.ndarray:
    """Decode any audio file to mono float32 at `sr` Hz."""
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise AnalysisError("ffmpeg isn't installed. Run: pip install imageio-ffmpeg")
    cmd = [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-i", str(path),
           "-t", str(int(max_seconds)), "-map", "0:a:0", "-ac", "1", "-ar", str(sr),
           "-f", "f32le", "pipe:1"]
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              timeout=300, **no_window_flags())
    except subprocess.TimeoutExpired as e:
        raise AnalysisError("Decoding took too long") from e
    except OSError as e:
        raise AnalysisError(f"Couldn't run ffmpeg: {e}") from e
    if proc.returncode != 0:
        lines = proc.stderr.decode("utf-8", "replace").strip().splitlines()
        raise AnalysisError("ffmpeg couldn't decode this file" + (f": {lines[-1]}" if lines else ""))
    y = np.frombuffer(proc.stdout, dtype="<f4")
    if y.size == 0:
        raise AnalysisError("The file contains no audio")
    return y


# ------------------------------------------------------------------------- spectra

def _stft_magnitude(y: np.ndarray, n_fft: int, hop: int) -> np.ndarray:
    if y.size < n_fft:
        y = np.pad(y, (0, n_fft - y.size))
    n_frames = 1 + (y.size - n_fft) // hop
    frames = np.lib.stride_tricks.as_strided(
        y, shape=(n_frames, n_fft), strides=(y.strides[0] * hop, y.strides[0]), writeable=False)
    window = np.hanning(n_fft).astype(np.float32)
    out = np.empty((n_frames, n_fft // 2 + 1), dtype=np.float32)
    step = max(1, 2_000_000 // n_fft)  # bound memory: ~32 MB of complex values per chunk
    for i in range(0, n_frames, step):
        out[i:i + step] = np.abs(np.fft.rfft(frames[i:i + step] * window, axis=1))
    return out


def onset_envelope(y: np.ndarray, sr: int = SR, n_fft: int = 1024, hop: int = 256) -> tuple[np.ndarray, float]:
    """Spectral flux on log-spaced bands, with the local mean removed. Returns (envelope, frames/s)."""
    mag = _stft_magnitude(y, n_fft, hop)
    freqs = np.fft.rfftfreq(n_fft, 1.0 / sr)
    edges = np.unique(np.clip(np.searchsorted(freqs, np.geomspace(30.0, 11000.0, 37)), 1, freqs.size - 1))
    bands = np.add.reduceat(mag[:, : edges[-1]], edges[:-1], axis=1)
    bands = np.log1p(1000.0 * bands / (bands.max() + 1e-9))
    flux = np.concatenate(([0.0], np.maximum(np.diff(bands, axis=0), 0.0).sum(axis=1)))
    fps = sr / hop
    width = max(3, int(round(0.4 * fps)) | 1)
    local_mean = np.convolve(flux, np.ones(width) / width, mode="same")
    env = np.maximum(flux - local_mean, 0.0)
    sd = env.std()
    return (env / sd if sd > 0 else env), fps


# --------------------------------------------------------------------------- tempo

def _autocorrelation(x: np.ndarray, max_lag: int) -> np.ndarray:
    x = x - x.mean()
    nfft = 1 << int(np.ceil(np.log2(2 * x.size)))
    spec = np.fft.rfft(x, nfft)
    ac = np.fft.irfft(spec * np.conj(spec), nfft)[: max_lag + 1]
    return ac / ac[0] if ac[0] > 0 else ac


def _comb_score(ac: np.ndarray, fps: float, bpms: np.ndarray, harmonics: int) -> np.ndarray:
    """Mean autocorrelation at 1..harmonics beat periods for each candidate tempo."""
    lags = np.outer(60.0 * fps / bpms, np.arange(1, harmonics + 1))
    valid = lags <= ac.size - 1
    values = np.interp(lags, np.arange(ac.size), ac)
    values[~valid] = 0.0
    return values.sum(axis=1) / np.maximum(valid.sum(axis=1), 1)


def estimate_bpm(env: np.ndarray, fps: float, lo: float = 60.0, hi: float = 200.0,
                 center: float = 125.0) -> float | None:
    if env.size < fps * 8 or not np.any(env):
        return None
    max_lag = min(env.size - 1, int(fps * 60.0 / lo * 32) + 2)
    ac = _autocorrelation(env, max_lag)
    grid = np.arange(lo, hi + 1e-9, 0.25)
    score = _comb_score(ac, fps, grid, 4)
    prior = np.exp(-0.5 * np.log2(grid / center) ** 2)  # one octave wide, around 125 BPM
    coarse = float(grid[int(np.argmax(np.maximum(score, 0.0) * prior))])
    fine = np.arange(coarse * 0.98, coarse * 1.02, 0.01)
    harmonics = int(max(4, min(32, (ac.size - 1) / (60.0 * fps / coarse))))
    fine_score = _comb_score(ac, fps, fine, harmonics)
    return float(fine[int(np.argmax(fine_score))])


def fold_bpm(bpm: float, lo: float = 70.0, hi: float = 190.0) -> float:
    while bpm < lo:
        bpm *= 2
    while bpm > hi:
        bpm /= 2
    return bpm


# ----------------------------------------------------------------------------- key

def chroma_profile(y: np.ndarray, sr: int = SR, fmin: float = 65.0, fmax: float = 2100.0) -> np.ndarray:
    """Average 12-bin pitch-class energy (C=0 ... B=11) over the excerpt."""
    mag = _stft_magnitude(y, 8192, 4096)
    freqs = np.fft.rfftfreq(8192, 1.0 / sr)
    sel = np.nonzero((freqs >= fmin) & (freqs <= fmax))[0]
    spec = mag[:, sel]
    spec = np.log1p(100.0 * spec / (spec.max() + 1e-9))
    notes = np.round(69 + 12 * np.log2(freqs[sel] / 440.0)).astype(int)
    uniq, inv = np.unique(notes, return_inverse=True)
    counts = np.bincount(inv).astype(np.float32)
    per_bin = np.zeros((sel.size, uniq.size), np.float32)
    per_bin[np.arange(sel.size), inv] = 1.0 / counts[inv]  # average bins within a semitone
    to_pitch_class = np.zeros((uniq.size, 12), np.float32)
    to_pitch_class[np.arange(uniq.size), uniq % 12] = 1.0
    chroma = (spec @ per_bin) @ to_pitch_class
    energy = chroma.sum(axis=1)
    if energy.size == 0 or energy.max() <= 0:
        return np.zeros(12)
    keep = energy > energy.max() * 0.05  # skip near-silent frames
    return (chroma[keep] / energy[keep, None]).mean(axis=0)


def estimate_key(chroma: np.ndarray) -> tuple[int, str, float] | None:
    """Best (tonic, mode, correlation) against the Krumhansl-Kessler profiles."""
    if not np.any(chroma) or np.ptp(chroma) < 1e-9:
        return None
    best: tuple[int, str, float] | None = None
    for tonic in range(12):
        for mode, profile in (("major", MAJOR_PROFILE), ("minor", MINOR_PROFILE)):
            r = float(np.corrcoef(np.roll(profile, tonic), chroma)[0, 1])
            if best is None or r > best[2]:
                best = (tonic, mode, r)
    return best


# ------------------------------------------------------------------------ waveform

def waveform_overview(y: np.ndarray, points: int = 240) -> list[float]:
    n = y.size // points
    if n == 0:
        return []
    blocks = np.abs(y[: n * points]).reshape(points, n)
    rms = np.sqrt((blocks * blocks).mean(axis=1))
    peak = float(rms.max())
    if peak <= 0:
        return [0.0] * points
    return [round(float(v), 3) for v in np.sqrt(rms / peak)]


# ---------------------------------------------------------------------------- main

def _middle(y: np.ndarray, sr: int, seconds: float) -> np.ndarray:
    n = int(seconds * sr)
    if y.size <= n:
        return y
    start = (y.size - n) // 2
    return y[start:start + n]


def analyze_signal(y: np.ndarray, sr: int = SR) -> dict:
    duration = y.size / sr
    if duration < 8:
        raise AnalysisError("The track is too short to analyse")
    excerpt = _middle(y, sr, EXCERPT_SECONDS)
    env, fps = onset_envelope(excerpt, sr)
    bpm = estimate_bpm(env, fps)
    key = estimate_key(chroma_profile(excerpt, sr))
    result: dict = {"waveform": waveform_overview(y)}
    if duration < MAX_DECODE_SECONDS - 1:
        result["duration"] = round(duration, 2)
    if bpm:
        result["bpm"] = round(fold_bpm(bpm), 1)
    if key:
        tonic, mode, r = key
        result.update(key=key_name(tonic, mode), camelot=camelot(tonic, mode), key_confidence=round(r, 3))
    return result


def analyze_file(path: Path | str) -> dict:
    """Returns {"bpm", "key", "camelot", "key_confidence", "waveform", "duration"} (keys may be missing)."""
    return analyze_signal(decode_audio(path))
