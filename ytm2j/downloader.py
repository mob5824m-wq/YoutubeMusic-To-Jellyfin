"""Core yt-dlp download logic: options building + playlist downloads."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadCancelled, DownloadError

from .config import Config
from .cookies import build_cookie_opts
from .models import Track
from .utils import ensure_dir, sanitize_folder


class CancelledError(Exception):
    """Raised internally when a download is cancelled by the user."""


@dataclass
class DownloadState:
    total: int = 0
    done: int = 0
    bytes_done: int = 0
    bytes_total: int = 0
    current_file: str = ""
    error: str = ""
    tracks: list[Track] = field(default_factory=list)


class PlaylistDownloader:
    def __init__(self, cfg: Config):
        self.cfg = cfg

    # ------------------------------------------------------------------ #
    def output_folder(self, folder: str) -> Path:
        base = Path(self.cfg.resolved_output_dir()).expanduser()
        return base / sanitize_folder(folder or "Untitled")

    # ------------------------------------------------------------------ #
    def build_opts(
        self,
        playlist_url: str,
        folder: str,
        *,
        progress_hook: Callable[[dict], None] | None = None,
        postprocessor_hook: Callable[[dict], None] | None = None,
        skip_download: bool = False,
    ) -> dict[str, Any]:
        """Build the yt-dlp options dict for one playlist download."""
        out_dir = self.output_folder(folder)
        fmt = (self.cfg.audio_format or "").lower()

        postprocessors: list[dict[str, Any]] = []
        if fmt:
            postprocessors.append(
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": fmt,
                    "preferredquality": str(self.cfg.audio_quality),
                }
            )
        if self.cfg.add_metadata:
            postprocessors.append(
                {"key": "FFmpegMetadata", "add_metadata": True, "add_chapters": False}
            )
        if self.cfg.embed_thumbnail:
            postprocessors.append({"key": "EmbedThumbnail", "already_have_thumbnail": False})

        opts: dict[str, Any] = {
            "format": "bestaudio/best",
            "outtmpl": str(out_dir / "%(title)s.%(ext)s"),
            "ignoreerrors": True,
            "noprogress": True,
            "no_warnings": False,
            "retries": 5,
            "fragment_retries": 5,
            "concurrent_fragment_downloads": 4,
            "postprocessors": postprocessors,
            "writethumbnail": bool(self.cfg.embed_thumbnail),
        }

        if fmt == "m4a":
            opts["merge_output_format"] = "mp4"
        elif fmt in ("opus", "webm"):
            opts["merge_output_format"] = "webm"

        ff = self.cfg.resolved_ffmpeg()
        if ff:
            opts["ffmpeg_location"] = ff

        opts.update(build_cookie_opts(self.cfg))

        if self.cfg.player_client:
            opts["extractor_args"] = {"youtube": {"player_client": [self.cfg.player_client]}}

        if not skip_download and not self.cfg.dry_run:
            archive = self.cfg.resolved_archive()
            if archive:
                opts["download_archive"] = archive

        if skip_download:
            opts["skip_download"] = True
            opts["quiet"] = True
        if progress_hook:
            opts["progress_hooks"] = [progress_hook]
        if postprocessor_hook:
            opts["postprocessor_hooks"] = [postprocessor_hook]
        return opts

    # ------------------------------------------------------------------ #
    @staticmethod
    def _tracks_from(info: dict[str, Any] | None) -> list[Track]:
        if not info:
            return []
        tracks: list[Track] = []
        for i, entry in enumerate(info.get("entries") or [], 1):
            if not entry:
                continue
            tracks.append(
                Track(
                    index=i,
                    title=entry.get("title") or "",
                    artist=entry.get("artist") or entry.get("uploader") or entry.get("channel") or "",
                    album=entry.get("album") or "",
                    duration=entry.get("duration") or 0,
                    url=entry.get("url") or entry.get("webpage_url") or "",
                )
            )
        return tracks

    # ------------------------------------------------------------------ #
    def fetch_tracks(self, playlist_url: str) -> tuple[str, list[Track]]:
        """Return ``(playlist_title, tracks)`` using a fast flat extraction."""
        opts = self.build_opts(playlist_url, "unused", skip_download=True)
        opts["extract_flat"] = True
        opts["lazy_playlist"] = True
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(playlist_url, download=False)
        if not info:
            # ignoreerrors swallows the failure; surface an empty result instead.
            return "", []
        title = info.get("title") or ""
        return title, self._tracks_from(info)

    # ------------------------------------------------------------------ #
    def download(
        self,
        playlist_url: str,
        folder: str,
        state: DownloadState,
        cancel_event=None,
        on_progress: Callable[[], None] | None = None,
    ) -> None:
        out_dir = ensure_dir(self.output_folder(folder))
        state.current_file = ""

        def progress_hook(d: dict[str, Any]) -> None:
            if cancel_event is not None and cancel_event.is_set():
                raise DownloadCancelled()
            status = d.get("status")
            if status == "downloading":
                state.bytes_done = d.get("downloaded_bytes") or state.bytes_done
                total = d.get("total_bytes") or d.get("total_bytes_estimate")
                if total:
                    state.bytes_total = total
            elif status == "finished":
                state.done += 1
                if d.get("filename"):
                    state.current_file = d["filename"]
            if on_progress:
                on_progress()

        def postprocessor_hook(ph: dict[str, Any]) -> None:
            if ph.get("status") == "finished":
                info = ph.get("info_dict") or {}
                rd = (info.get("requested_downloads") or [{}])[0]
                fp = rd.get("filepath")
                if fp:
                    state.current_file = fp
                if on_progress:
                    on_progress()

        opts = self.build_opts(
            playlist_url,
            folder,
            progress_hook=progress_hook,
            postprocessor_hook=postprocessor_hook,
        )

        try:
            with YoutubeDL(opts) as ydl:
                info = ydl.extract_info(playlist_url, download=False)
                if info is None:
                    raise DownloadError("No information returned for playlist")
                if not state.tracks:
                    state.tracks = self._tracks_from(info)
                state.total = len(state.tracks) or info.get("playlist_count") or 0
                ydl.process_ie_result(info, download=True)
        except DownloadCancelled:
            # The cancel hook fired mid-download.
            raise CancelledError() from None

        if cancel_event is not None and cancel_event.is_set():
            raise CancelledError()

    # ------------------------------------------------------------------ #
    def folder_for(self, playlist) -> str:
        template = (self.cfg.playlist_folder_template or "").strip()
        if not template:
            return sanitize_folder(playlist.title or playlist.id or "Untitled")
        return sanitize_folder(
            template.replace("%(playlist_title)s", playlist.title or "")
            .replace("%(playlist_id)s", playlist.id or "")
        )
