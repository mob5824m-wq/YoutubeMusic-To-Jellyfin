"""End-to-end flow tests with yt-dlp replaced by a fake.

The sandbox cannot reach YouTube, so these tests inject a fake YoutubeDL /
downloader to validate the wiring: option building, progress accounting,
cancellation, folder creation and the background job runner.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from ytm2j.config import Config
from ytm2j.db import StateDB
from ytm2j.downloader import CancelledError, DownloadState, PlaylistDownloader
from ytm2j.models import Job, JobStatus, Playlist, Track
from ytm2j.runner import JobRunner


class FakeYDL:
    """Stands in for yt_dlp.YoutubeDL, firing the progress hooks itself."""

    def __init__(self, opts):
        self.opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, url, download=False):
        return {
            "title": "Road Trip",
            "playlist_count": 2,
            "entries": [
                {"title": "Song A", "url": "u1", "artist": "Artist A"},
                {"title": "Song B", "url": "u2", "artist": "Artist B"},
            ],
        }

    def process_ie_result(self, info, download=True):
        assert download is True
        # Real yt-dlp fires progress hooks once per downloaded item.
        for i in range(len(info["entries"])):
            for hook in self.opts.get("progress_hooks", []):
                hook({"status": "downloading", "downloaded_bytes": 1000, "total_bytes": 2000})
                hook({"status": "finished", "filename": f"/out/Song {i}.mp3"})
        for hook in self.opts.get("postprocessor_hooks", []):
            hook({
                "status": "finished",
                "info_dict": {"requested_downloads": [{"filepath": "/out/Song 0.mp3"}]},
            })


def test_download_updates_state_and_creates_folder(tmp_path, monkeypatch):
    cfg = Config()
    cfg.output_dir = str(tmp_path / "music")
    monkeypatch.setattr("ytm2j.downloader.YoutubeDL", FakeYDL)

    dl = PlaylistDownloader(cfg)
    state = DownloadState()
    dl.download("https://www.youtube.com/playlist?list=PLx", "Road Trip", state)

    assert state.total == 2
    assert state.done == 2
    assert state.tracks[0].title == "Song A"
    assert state.tracks[1].artist == "Artist B"
    assert (Path(cfg.output_dir) / "Road Trip").is_dir()
    assert state.current_file.endswith(".mp3")


def test_download_cancel_raises_cancelled(tmp_path, monkeypatch):
    cfg = Config()
    cfg.output_dir = str(tmp_path / "music")
    monkeypatch.setattr("ytm2j.downloader.YoutubeDL", FakeYDL)

    dl = PlaylistDownloader(cfg)
    ev = threading.Event()
    ev.set()
    with pytest.raises(CancelledError):
        dl.download("u", "F", DownloadState(), ev)


class FakeDownloader(PlaylistDownloader):
    def fetch_tracks(self, url):
        return "Fake Playlist", [Track(index=1, title="Song", artist="Artist", url="u", duration=100)]

    def download(self, url, folder, state, cancel_event=None, on_progress=None):
        state.total = 1
        state.done = 1
        state.current_file = f"{folder}/Song.mp3"
        if on_progress:
            on_progress()


class BlockingDownloader(FakeDownloader):
    def download(self, url, folder, state, cancel_event=None, on_progress=None):
        state.total = 1
        state.done = 0
        if cancel_event is not None:
            cancel_event.wait(timeout=10)
        raise CancelledError()


def _runner(tmp_path, downloader):
    cfg = Config()
    cfg.output_dir = str(tmp_path / "music")
    db = StateDB(str(tmp_path / "state.db"))
    runner = JobRunner(cfg, db, downloader)
    runner.start()
    return runner, db


def test_runner_completes_job(tmp_path):
    runner, db = _runner(tmp_path, FakeDownloader(Config()))
    job = runner.enqueue(Playlist(title="Fake Playlist", url="https://www.youtube.com/playlist?list=PLFake"))
    runner.drain(timeout=15)
    runner.stop()

    j = db.get_job(job.id)
    assert j.status == JobStatus.DONE.value
    assert j.total == 1 and j.done == 1


def test_runner_cancels_running_job(tmp_path):
    cfg = Config()
    cfg.output_dir = str(tmp_path / "music")
    runner, db = _runner(tmp_path, BlockingDownloader(cfg))
    job = runner.enqueue(Playlist(title="Blocking", url="https://www.youtube.com/playlist?list=PLB"))
    import time

    time.sleep(0.3)
    assert runner.cancel(job.id) is True
    runner.drain(timeout=15)
    runner.stop()
    assert db.get_job(job.id).status == JobStatus.CANCELLED.value


def test_runner_resumes_queued_jobs(tmp_path):
    cfg = Config()
    cfg.output_dir = str(tmp_path / "music")
    db = StateDB(str(tmp_path / "state.db"))
    db.create_job(
        Job(
            id="stale1", url="u", title="T", folder="T", status=JobStatus.QUEUED.value,
            created_at="x", updated_at="x",
        )
    )
    runner = JobRunner(cfg, db, FakeDownloader(cfg))
    assert runner.resume_queued() == 1
    runner.start()
    runner.drain(timeout=15)
    runner.stop()
    assert db.get_job("stale1").status == JobStatus.DONE.value


class NoneYDL(FakeYDL):
    """extract_info returns None (what ignoreerrors yields for a bad playlist)."""

    def extract_info(self, url, download=False):
        return None


def test_fetch_tracks_handles_none(tmp_path, monkeypatch):
    cfg = Config()
    cfg.output_dir = str(tmp_path / "music")
    monkeypatch.setattr("ytm2j.downloader.YoutubeDL", NoneYDL)
    dl = PlaylistDownloader(cfg)
    title, tracks = dl.fetch_tracks("u")
    assert title == ""
    assert tracks == []


class EmptyDownloader(FakeDownloader):
    def fetch_tracks(self, url):
        return "", []


def test_runner_marks_unreadable_playlist_as_error(tmp_path):
    cfg = Config()
    cfg.output_dir = str(tmp_path / "music")
    db = StateDB(str(tmp_path / "state.db"))
    runner = JobRunner(cfg, db, EmptyDownloader(cfg))
    runner.start()
    job = runner.enqueue(Playlist(title="Bad", url="https://www.youtube.com/playlist?list=PLbad"))
    runner.drain(timeout=15)
    runner.stop()
    j = db.get_job(job.id)
    assert j.status == JobStatus.ERROR.value
    assert "empty or could not be read" in j.error
