"""Locate an ffmpeg binary (system install first, then the imageio-ffmpeg wheel)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from functools import lru_cache


@lru_cache(maxsize=1)
def find_ffmpeg() -> str | None:
    override = os.environ.get("CRATEDIGGER_FFMPEG")
    if override and os.path.isfile(override):
        return override
    system = shutil.which("ffmpeg")
    if system:
        return system
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # not installed, or no binary for this platform
        return None


def no_window_flags() -> dict:
    """subprocess kwargs that stop Windows from flashing a console window."""
    if sys.platform == "win32":
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)}
    return {}
