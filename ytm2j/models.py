"""Domain models shared by the CLI, server and downloader."""

from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any

# YouTube playlist IDs used by the account's "special" library entries.
SPECIAL_PLAYLISTS = {
    # music.youtube.com / youtube.com list id -> human label + kind
    "LM": "Liked Songs",
    "LL": "Liked Videos",
    "WL": "Watch Later",
    "HL": "History",
    "UU": "Uploads",
    "UL": "Subscriptions",
}

_URL_RE = re.compile(
    r"(?:https?://)?(?:www\.|m\.|music\.)?(?:youtube\.com|youtu\.be)/",
    re.IGNORECASE,
)


def normalize_playlist_url(value: str) -> str:
    """Best-effort conversion of arbitrary user input into a playlist URL."""
    value = (value or "").strip()
    if not value:
        return ""
    if _URL_RE.match(value):
        # Allow a bare "?list=ID" or "list=ID" pasted by users.
        if "list=" in value:
            return value
        return value
    # Bare "PL..." / "LL" / "LM" id.
    if re.fullmatch(r"[A-Za-z0-9_-]{6,}", value):
        return f"https://www.youtube.com/playlist?list={value}"
    # A "@handle" or channel id -> channel playlists tab.
    if value.startswith("@") or value.startswith("UC"):
        return f"https://www.youtube.com/{value}/playlists"
    return f"https://www.youtube.com/playlist?list={value}"


def playlist_id_from_url(url: str) -> str:
    """Extract the canonical list id from a playlist URL ('' if unknown)."""
    if not url:
        return ""
    m = re.search(r"[?&]list=([A-Za-z0-9_-]+)", url)
    if m:
        return m.group(1)
    m = re.search(r"/playlist/([A-Za-z0-9_-]+)", url)
    if m:
        return m.group(1)
    if "youtu.be/" in url:
        return ""
    return ""


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    DONE = "done"
    ERROR = "error"
    CANCELLED = "cancelled"


TERMINAL = {JobStatus.DONE, JobStatus.ERROR, JobStatus.CANCELLED}


@dataclass
class Track:
    index: int
    title: str
    artist: str = ""
    url: str = ""
    duration: int = 0
    album: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Playlist:
    title: str
    url: str
    id: str = ""
    kind: str = "manual"  # discovered | special | manual | channel
    entry_count: int = 0
    folder: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            self.id = playlist_id_from_url(self.url)
        if not self.folder:
            from .utils import sanitize_folder
            self.folder = sanitize_folder(self.title or self.id)

    @property
    def is_special(self) -> bool:
        return self.kind == "special"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["is_special"] = self.is_special
        return d


@dataclass
class Job:
    id: str
    url: str
    title: str
    folder: str
    status: str = JobStatus.QUEUED.value
    total: int = 0
    done: int = 0
    bytes_done: int = 0
    bytes_total: int = 0
    current_file: str = ""
    error: str = ""
    created_at: str = ""
    updated_at: str = ""
    tracks: list[Track] = field(default_factory=list)

    def progress(self) -> float:
        if not self.total:
            return 0.0
        return min(1.0, self.done / self.total)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["progress"] = self.progress()
        return d

    def to_summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "url": self.url,
            "title": self.title,
            "folder": self.folder,
            "status": self.status,
            "total": self.total,
            "done": self.done,
            "bytes_done": self.bytes_done,
            "bytes_total": self.bytes_total,
            "current_file": self.current_file,
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
