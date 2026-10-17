"""Search-result model shared by all music sources."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class Track:
    source: str  # "youtube" | "jamendo" | "archive"
    source_id: str
    title: str
    artist: str
    album: str = ""
    duration: float | None = None  # seconds
    genres: list[str] = field(default_factory=list)
    license: str = ""  # short label, e.g. "CC BY-NC 3.0"
    license_url: str = ""
    page_url: str = ""  # human-facing page, used for attribution
    preview_url: str = ""  # direct audio stream the browser can play ("" for YouTube)
    image_url: str = ""
    year: str = ""
    # Downloadable files, best first: {"ext": ".flac", "url": "...", "lossless": True}
    downloads: list[dict] = field(default_factory=list)
    waveform: list[float] | None = None  # 0..1 peaks, when the source provides them
    extra: dict = field(default_factory=dict)

    @property
    def uid(self) -> str:
        return f"{self.source}:{self.source_id}"

    def to_public(self) -> dict:
        """JSON for the browser. Download URLs stay on the server."""
        data = asdict(self)
        data.pop("downloads")
        data["uid"] = self.uid
        data["lossless_available"] = any(d.get("lossless") for d in self.downloads)
        return data
