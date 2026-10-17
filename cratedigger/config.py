"""User settings, stored as JSON in the platform's config folder."""

from __future__ import annotations

import json
import os
import sys
import threading
from dataclasses import asdict, dataclass, fields, replace
from pathlib import Path

# Browsers yt-dlp can borrow YouTube cookies from ("" means don't).
COOKIE_BROWSERS = ("", "brave", "chrome", "chromium", "edge", "firefox", "opera", "safari", "vivaldi", "whale")


def config_dir() -> Path:
    override = os.environ.get("CRATEDIGGER_HOME")
    if override:
        return Path(override).expanduser()
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "CrateDigger"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "CrateDigger"
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "cratedigger"


def default_library_dir() -> Path:
    return Path.home() / "Music" / "Crate Digger"


@dataclass
class Settings:
    library_dir: str = ""
    jamendo_client_id: str = ""
    prefer_lossless: bool = False  # FLAC from Jamendo / Internet Archive when offered
    auto_analyze: bool = True  # detect BPM and key after each download
    youtube_cookies_browser: str = ""

    @property
    def library_path(self) -> Path:
        return Path(self.library_dir).expanduser() if self.library_dir else default_library_dir()


class SettingsStore:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self._settings = self._load()

    def _load(self) -> Settings:
        settings = Settings()
        try:
            data = json.loads(self.path.read_text("utf-8"))
        except (OSError, ValueError):
            data = {}
        if isinstance(data, dict):
            for f in fields(Settings):
                value = data.get(f.name)
                if isinstance(value, type(getattr(settings, f.name))):
                    setattr(settings, f.name, value)
        if not settings.library_dir:
            settings.library_dir = str(default_library_dir())
        if not settings.jamendo_client_id:
            settings.jamendo_client_id = os.environ.get("JAMENDO_CLIENT_ID", "").strip()
        if settings.youtube_cookies_browser not in COOKIE_BROWSERS:
            settings.youtube_cookies_browser = ""
        return settings

    def get(self) -> Settings:
        with self._lock:
            return replace(self._settings)

    def update(self, changes: dict) -> Settings:
        """Validate and save changes. Raises ValueError with a user-facing message."""
        with self._lock:
            new = replace(self._settings)
            for f in fields(Settings):
                if f.name not in changes:
                    continue
                value = changes[f.name]
                expected = type(getattr(new, f.name))
                if not isinstance(value, expected):
                    raise ValueError(f"{f.name} must be a {expected.__name__}")
                if isinstance(value, str):
                    value = value.strip()
                setattr(new, f.name, value)

            if new.youtube_cookies_browser not in COOKIE_BROWSERS:
                raise ValueError("Unknown browser for YouTube cookies")
            if not new.library_dir:
                new.library_dir = str(default_library_dir())
            library = Path(new.library_dir).expanduser()
            try:
                library.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                raise ValueError(f"Can't use {library} as the library folder: {e.strerror or e}") from e
            new.library_dir = str(library)

            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(asdict(new), indent=2), "utf-8")
            os.replace(tmp, self.path)
            self._settings = new
            return replace(new)
