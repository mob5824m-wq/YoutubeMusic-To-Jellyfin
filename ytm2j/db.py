"""SQLite-backed job / playlist registry.

Keeps durable state so the web UI (and a restart) can show what has been
queued, what finished, and what already downloaded. yt-dlp's own
``--download-archive`` is still used for per-track dedupe; this store is
for the app's own bookkeeping.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable

from .config import platform_config_dir
from .models import Job, Playlist, Track, JobStatus
from .utils import now_iso

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id            TEXT PRIMARY KEY,
    url           TEXT,
    title         TEXT,
    folder        TEXT,
    status        TEXT,
    total         INTEGER DEFAULT 0,
    done          INTEGER DEFAULT 0,
    bytes_done    INTEGER DEFAULT 0,
    bytes_total   INTEGER DEFAULT 0,
    current_file  TEXT,
    error         TEXT,
    tracks_json   TEXT,
    created_at    TEXT,
    updated_at    TEXT
);
CREATE TABLE IF NOT EXISTS playlists (
    id         TEXT PRIMARY KEY,
    url        TEXT,
    title      TEXT,
    kind       TEXT,
    entry_count INTEGER DEFAULT 0,
    folder     TEXT,
    added_at   TEXT
);
CREATE TABLE IF NOT EXISTS logs (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    ts       TEXT,
    level    TEXT,
    message  TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
"""


class StateDB:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else (platform_config_dir() / "ytm2j.db")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(_SCHEMA)

    # ------------------------------------------------------------------ #
    # Jobs
    # ------------------------------------------------------------------ #
    def create_job(self, job: Job) -> Job:
        with self._lock, self._connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO jobs
                   (id, url, title, folder, status, total, done, bytes_done,
                    bytes_total, current_file, error, tracks_json, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    job.id, job.url, job.title, job.folder, job.status, job.total,
                    job.done, job.bytes_done, job.bytes_total, job.current_file,
                    job.error, json.dumps([t.to_dict() for t in job.tracks]),
                    job.created_at, job.updated_at,
                ),
            )
        return job

    def update_job(self, job_id: str, **fields: Any) -> None:
        fields["updated_at"] = now_iso()
        if "tracks" in fields:
            fields["tracks_json"] = json.dumps(
                [t.to_dict() if isinstance(t, Track) else t for t in fields.pop("tracks")]
            )
        cols = ", ".join(f"{k} = ?" for k in fields)
        with self._lock, self._connect() as conn:
            conn.execute(f"UPDATE jobs SET {cols} WHERE id = ?", (*fields.values(), job_id))

    def get_job(self, job_id: str) -> Job | None:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return self._row_to_job(row) if row else None

    def list_jobs(self, limit: int | None = None) -> list[Job]:
        with self._lock, self._connect() as conn:
            q = "SELECT * FROM jobs ORDER BY created_at DESC"
            if limit:
                q += f" LIMIT {int(limit)}"
            rows = conn.execute(q).fetchall()
        return [self._row_to_job(r) for r in rows]

    def delete_job(self, job_id: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))

    def clear_finished(self) -> int:
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM jobs WHERE status IN (?,?,?)",
                (JobStatus.DONE.value, JobStatus.ERROR.value, JobStatus.CANCELLED.value),
            )
            return cur.rowcount or 0

    @staticmethod
    def _row_to_job(row: sqlite3.Row) -> Job:
        tracks = []
        if row["tracks_json"]:
            try:
                tracks = [Track(**t) for t in json.loads(row["tracks_json"])]
            except (json.JSONDecodeError, TypeError):
                tracks = []
        return Job(
            id=row["id"],
            url=row["url"] or "",
            title=row["title"] or "",
            folder=row["folder"] or "",
            status=row["status"] or JobStatus.QUEUED.value,
            total=row["total"] or 0,
            done=row["done"] or 0,
            bytes_done=row["bytes_done"] or 0,
            bytes_total=row["bytes_total"] or 0,
            current_file=row["current_folder"] if "current_folder" in row.keys() else (row["current_file"] or ""),
            error=row["error"] or "",
            created_at=row["created_at"] or "",
            updated_at=row["updated_at"] or "",
            tracks=tracks,
        )

    # ------------------------------------------------------------------ #
    # Playlists
    # ------------------------------------------------------------------ #
    def save_playlists(self, playlists: Iterable[Playlist]) -> None:
        ts = now_iso()
        with self._lock, self._connect() as conn:
            for pl in playlists:
                conn.execute(
                    "INSERT OR REPLACE INTO playlists (id, url, title, kind, entry_count, folder, added_at) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (pl.id or pl.url, pl.url, pl.title, pl.kind, pl.entry_count, pl.folder, ts),
                )

    def list_playlists(self) -> list[Playlist]:
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT * FROM playlists ORDER BY kind DESC, title COLLATE NOCASE").fetchall()
        return [
            Playlist(
                id=r["id"], url=r["url"], title=r["title"], kind=r["kind"],
                entry_count=r["entry_count"] or 0, folder=r["folder"] or "",
            )
            for r in rows
        ]

    def add_playlist(self, pl: Playlist) -> None:
        ts = now_iso()
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO playlists (id, url, title, kind, entry_count, folder, added_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (pl.id or pl.url, pl.url, pl.title, pl.kind, pl.entry_count, pl.folder, ts),
            )

    def remove_playlist(self, playlist_id: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM playlists WHERE id = ?", (playlist_id,))

    # ------------------------------------------------------------------ #
    # Logs
    # ------------------------------------------------------------------ #
    def add_log(self, message: str, level: str = "info") -> None:
        with self._lock, self._connect() as conn:
            conn.execute("INSERT INTO logs (ts, level, message) VALUES (?,?,?)", (now_iso(), level, message))

    def list_logs(self, limit: int = 200) -> list[dict[str, str]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT * FROM logs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [{"ts": r["ts"], "level": r["level"], "message": r["message"]} for r in rows]
