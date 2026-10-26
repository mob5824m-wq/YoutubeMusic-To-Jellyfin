"""Background job queue that drives playlist downloads.

Jobs are persisted in SQLite so the UI survives restarts. A small pool of
worker threads drains the queue; the number of workers is configurable
via ``Config.concurrent``.
"""

from __future__ import annotations

import queue
import threading
import time
import uuid
from typing import Iterable

from .config import Config
from .db import StateDB
from .downloader import CancelledError, DownloadState, PlaylistDownloader
from .models import Job, JobStatus, Playlist, Track, playlist_id_from_url
from yt_dlp.utils import DownloadError

from .utils import now_iso


class JobRunner:
    def __init__(
        self,
        cfg: Config,
        db: StateDB,
        downloader: PlaylistDownloader | None = None,
    ):
        self.cfg = cfg
        self.db = db
        self.downloader = downloader or PlaylistDownloader(cfg)
        self._queue: "queue.Queue[Job | None]" = queue.Queue()
        self._cancels: dict[str, threading.Event] = {}
        self._cancelled: set[str] = set()
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._started = False

    # ------------------------------------------------------------------ #
    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._stop.clear()
        self._cancelled.clear()
        workers = max(1, int(self.cfg.concurrent or 1))
        for i in range(workers):
            t = threading.Thread(target=self._worker, name=f"ytm2j-worker-{i}", daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        for _ in self._cancels.values():
            _.set()
        for t in self._threads:
            t.join(timeout=timeout)
        self._threads.clear()
        self._started = False

    # ------------------------------------------------------------------ #
    def enqueue(self, playlist: Playlist) -> Job:
        folder = self.downloader.folder_for(playlist)
        playlist.folder = folder
        job = Job(
            id=uuid.uuid4().hex[:12],
            url=playlist.url,
            title=playlist.title or playlist.id,
            folder=folder,
            status=JobStatus.QUEUED.value,
            created_at=now_iso(),
            updated_at=now_iso(),
        )
        self.db.create_job(job)
        self._queue.put(job)
        return job

    def enqueue_many(self, playlists: Iterable[Playlist]) -> list[Job]:
        return [self.enqueue(p) for p in playlists]

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            self._cancelled.add(job_id)
        ev = self._cancels.get(job_id)
        if ev:
            ev.set()
            return True
        job = self.db.get_job(job_id)
        if job and job.status == JobStatus.QUEUED.value:
            self.db.update_job(job_id, status=JobStatus.CANCELLED.value)
            return True
        return False

    def remove(self, job_id: str) -> None:
        self.cancel(job_id)
        self.db.delete_job(job_id)

    # ------------------------------------------------------------------ #
    def _worker(self) -> None:
        while not self._stop.is_set():
            try:
                job = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                if job is None:
                    break
                if job.id in self._cancelled:
                    self.db.update_job(job.id, status=JobStatus.CANCELLED.value)
                    continue
                self._run_job(job)
            except Exception as exc:  # never let a worker die
                self.db.update_job(job.id, status=JobStatus.ERROR.value, error=str(exc))
            finally:
                self._queue.task_done()

    # ------------------------------------------------------------------ #
    def _get_tracks(self, job: Job) -> tuple[str, list[Track]]:
        """Prefer the YouTube Data API (Google OAuth) for track listings."""
        try:
            from .ytapi import api_for_config

            api = api_for_config(self.cfg)
            if api:
                pid = playlist_id_from_url(job.url)
                if pid:
                    tracks = api.list_playlist_items(pid)
                    if tracks:
                        return job.title or pid, tracks
        except Exception:
            pass
        return self.downloader.fetch_tracks(job.url)

    # ------------------------------------------------------------------ #
    def _run_job(self, job: Job) -> None:
        cancel_event = threading.Event()
        with self._lock:
            self._cancels[job.id] = cancel_event

        state = DownloadState()
        throttle = {"last": 0.0}

        def persist() -> None:
            now = time.time()
            if now - throttle["last"] < 1.0:
                return
            throttle["last"] = now
            self.db.update_job(
                job.id,
                done=state.done,
                bytes_done=state.bytes_done,
                bytes_total=state.bytes_total,
                current_file=state.current_file,
            )

        self.db.update_job(job.id, status=JobStatus.RUNNING.value, error="")
        try:
            title, tracks = self._get_tracks(job)
            if not tracks:
                raise DownloadError(
                    "Playlist is empty or could not be read. "
                    "Check the URL/ID and that your cookie source still works."
                )
            if title and not job.title:
                job.title = title
            state.tracks = tracks
            state.total = len(tracks)
            self.db.update_job(job.id, total=state.total, tracks=tracks, title=job.title)

            self.downloader.download(job.url, job.folder, state, cancel_event, on_progress=persist)
        except CancelledError:
            self.db.update_job(
                job.id,
                status=JobStatus.CANCELLED.value,
                done=state.done,
                current_file=state.current_file,
            )
            return
        except Exception as exc:
            self.db.update_job(
                job.id,
                status=JobStatus.ERROR.value,
                error=str(exc) or exc.__class__.__name__,
                done=state.done,
                current_file=state.current_file,
            )
            return
        finally:
            with self._lock:
                self._cancels.pop(job.id, None)

        if job.id in self._cancelled:
            self.db.update_job(
                job.id,
                status=JobStatus.CANCELLED.value,
                done=state.done,
                current_file=state.current_file,
            )
            return
        self.db.update_job(
            job.id,
            status=JobStatus.DONE.value,
            done=state.done,
            total=state.total,
            current_file="",
            bytes_done=state.bytes_done,
            bytes_total=state.bytes_total,
            error="",
        )

    # ------------------------------------------------------------------ #
    def is_busy(self) -> bool:
        return any(t.is_alive() for t in self._threads)

    def drain(self, timeout: float | None = None) -> None:
        """Block until every queued job has been processed."""
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            if self._queue.unfinished_tasks == 0:
                return
            if deadline is not None and time.monotonic() > deadline:
                return
            time.sleep(0.1)

    # ------------------------------------------------------------------ #
    def resume_queued(self) -> int:
        """Re-queue any jobs that were still pending when the app stopped."""
        pending = [j for j in self.db.list_jobs() if j.status in (JobStatus.QUEUED.value, JobStatus.RUNNING.value)]
        for job in pending:
            self.db.update_job(job.id, status=JobStatus.QUEUED.value)
            self._queue.put(job)
        return len(pending)
