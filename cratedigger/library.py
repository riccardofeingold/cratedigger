"""The crate: an SQLite index of the tracks you've collected."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from .audiofile import is_audio_path
from .naming import parse_filename

SCHEMA = """
CREATE TABLE IF NOT EXISTS tracks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    path            TEXT NOT NULL UNIQUE,
    source          TEXT NOT NULL DEFAULT 'local',
    source_id       TEXT NOT NULL DEFAULT '',
    title           TEXT NOT NULL DEFAULT '',
    artist          TEXT NOT NULL DEFAULT '',
    album           TEXT NOT NULL DEFAULT '',
    genre           TEXT NOT NULL DEFAULT '',
    year            TEXT NOT NULL DEFAULT '',
    duration        REAL,
    bpm             REAL,
    musical_key     TEXT NOT NULL DEFAULT '',
    camelot         TEXT NOT NULL DEFAULT '',
    waveform        TEXT,
    license         TEXT NOT NULL DEFAULT '',
    license_url     TEXT NOT NULL DEFAULT '',
    page_url        TEXT NOT NULL DEFAULT '',
    image_url       TEXT NOT NULL DEFAULT '',
    added_at        TEXT NOT NULL,
    analysis_status TEXT NOT NULL DEFAULT 'pending',  -- pending|running|done|error|skipped
    analysis_error  TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_tracks_source ON tracks (source, source_id);
"""

COLUMNS = ("path", "source", "source_id", "title", "artist", "album", "genre", "year", "duration",
           "bpm", "musical_key", "camelot", "waveform", "license", "license_url", "page_url",
           "image_url", "added_at", "analysis_status", "analysis_error")
_LIST_COLUMNS = ", ".join(["id", *(c for c in COLUMNS if c != "waveform")])
_TEXT_COLUMNS = {"source", "source_id", "title", "artist", "album", "genre", "year", "musical_key",
                 "camelot", "license", "license_url", "page_url", "image_url", "analysis_status",
                 "analysis_error"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Library:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(db_path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.executescript(SCHEMA)
            self._db.commit()

    # ------------------------------------------------------------------ writes

    def _clean(self, fields: dict) -> dict:
        out = {}
        for k, v in fields.items():
            if k not in COLUMNS:
                continue
            if k == "waveform" and isinstance(v, list):
                v = json.dumps(v)
            if v is None and k in _TEXT_COLUMNS:
                v = ""
            out[k] = v
        return out

    def add(self, path: Path, **fields) -> int:
        """Insert a track (or refresh the row for the same file). Returns its id."""
        fields = self._clean(fields)
        fields["path"] = str(Path(path).resolve())
        fields.setdefault("added_at", _now())
        cols = list(fields)
        updates = ", ".join(f"{c} = excluded.{c}" for c in cols if c not in ("path", "added_at"))
        conflict = f"DO UPDATE SET {updates}" if updates else "DO NOTHING"
        sql = (f"INSERT INTO tracks ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))}) "
               f"ON CONFLICT(path) {conflict}")
        with self._lock:
            self._db.execute(sql, [fields[c] for c in cols])
            self._db.commit()
            return self._db.execute("SELECT id FROM tracks WHERE path = ?", (fields["path"],)).fetchone()[0]

    def update(self, track_id: int, **fields) -> None:
        fields = self._clean(fields)
        fields.pop("path", None)
        if not fields:
            return
        sql = f"UPDATE tracks SET {', '.join(f'{c} = ?' for c in fields)} WHERE id = ?"
        with self._lock:
            self._db.execute(sql, [*fields.values(), track_id])
            self._db.commit()

    def delete(self, track_id: int) -> None:
        with self._lock:
            self._db.execute("DELETE FROM tracks WHERE id = ?", (track_id,))
            self._db.commit()

    # ------------------------------------------------------------------- reads

    def get(self, track_id: int) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM tracks WHERE id = ?", (track_id,)).fetchone()
        if row is None:
            return None
        track = dict(row)
        try:
            track["waveform"] = json.loads(track["waveform"]) if track["waveform"] else None
        except ValueError:
            track["waveform"] = None
        return track

    def list_tracks(self) -> list[dict]:
        with self._lock:
            rows = self._db.execute(f"SELECT {_LIST_COLUMNS} FROM tracks ORDER BY added_at DESC, id DESC").fetchall()
        return [dict(r) for r in rows]

    def find_by_source(self, source: str, source_id: str) -> dict | None:
        with self._lock:
            row = self._db.execute(f"SELECT {_LIST_COLUMNS} FROM tracks WHERE source = ? AND source_id = ?",
                                   (source, source_id)).fetchone()
        return dict(row) if row else None

    def known_uids(self) -> set[str]:
        with self._lock:
            rows = self._db.execute("SELECT source, source_id FROM tracks WHERE source_id != ''").fetchall()
        return {f"{r[0]}:{r[1]}" for r in rows}

    def known_paths(self) -> set[str]:
        with self._lock:
            return {r[0] for r in self._db.execute("SELECT path FROM tracks").fetchall()}

    def ids_needing_analysis(self) -> list[int]:
        with self._lock:
            rows = self._db.execute(
                "SELECT id FROM tracks WHERE analysis_status IN ('pending', 'running') ORDER BY id").fetchall()
        return [r[0] for r in rows]

    # ------------------------------------------------------------------ syncing

    def prune_missing(self) -> int:
        """Forget tracks whose files were deleted or moved outside the app."""
        with self._lock:
            rows = self._db.execute("SELECT id, path FROM tracks").fetchall()
            gone = [r[0] for r in rows if not Path(r[1]).exists()]
            self._db.executemany("DELETE FROM tracks WHERE id = ?", [(i,) for i in gone])
            self._db.commit()
        return len(gone)

    def rescan(self, folder: Path) -> dict:
        """Pick up audio files added to the folder by hand and drop vanished ones.

        Returns {"added", "removed", "to_analyze": [ids]}. Existing BPM/key tags
        (e.g. from rekordbox) are imported and kept.
        """
        from .analysis import parse_key  # numpy-backed module, imported lazily
        from .tagging import read_tags

        removed = self.prune_missing()
        known = self.known_paths()
        added, to_analyze = 0, []
        if folder.is_dir():
            for p in sorted(folder.rglob("*")):
                try:
                    rel_parts = p.relative_to(folder).parts
                except ValueError:
                    continue
                if any(part.startswith(".") for part in rel_parts) or not p.is_file() or not is_audio_path(p):
                    continue
                if str(p.resolve()) in known:
                    continue
                try:
                    tags = read_tags(p)
                except Exception:  # mutagen missing or unreadable file
                    tags = {}
                artist, title = parse_filename(p.stem)
                key = parse_key(tags.get("key"))
                track_id = self.add(
                    p, source="local", title=tags.get("title") or title,
                    artist=tags.get("artist") or artist, album=tags.get("album", ""),
                    genre=tags.get("genre", ""), year=tags.get("year", ""),
                    duration=tags.get("duration"), bpm=tags.get("bpm"),
                    musical_key=key[0] if key else "", camelot=key[1] if key else "",
                    analysis_status="pending")
                added += 1
                to_analyze.append(track_id)
        return {"added": added, "removed": removed, "to_analyze": to_analyze}

    def close(self) -> None:
        with self._lock:
            self._db.close()
