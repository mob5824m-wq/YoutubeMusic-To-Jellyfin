"""Account playlist discovery.

Enumerates the playlists belonging to a YouTube account. The YouTube
"library" feed (``/feed/library``) lists the account's special entries
(Liked Songs, Watch Later, ...) plus a link to ``/feed/playlists`` which
lists every user-created playlist. We try that path first, then fall back
to hitting ``/feed/playlists`` directly, then to a channel's playlists tab.

Discovery is best-effort: YouTube changes these endpoints, so failures are
surfaced as a helpful ``DiscoveryError`` and the user can always paste
playlist URLs manually.
"""

from __future__ import annotations

from typing import Any, Iterable

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

from .cookies import build_cookie_opts
from .models import Playlist, playlist_id_from_url


class DiscoveryError(Exception):
    """Raised when no playlists could be discovered."""


def _base_opts(cfg, *, flat: bool = True) -> dict[str, Any]:
    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "ignoreerrors": True,
        "skip_download": True,
        "extract_flat": flat,
    }
    if flat:
        opts["lazy_playlist"] = True
    opts.update(build_cookie_opts(cfg))
    return opts


def _extract(url: str, cfg, *, flat: bool = True) -> dict[str, Any] | None:
    try:
        with YoutubeDL(_base_opts(cfg, flat=flat)) as ydl:
            return ydl.extract_info(url, download=False)
    except DownloadError:
        return None
    except Exception:
        return None


def _entry_to_playlist(entry: dict[str, Any], kind: str) -> Playlist | None:
    if not entry:
        return None
    url = entry.get("url") or ""
    title = entry.get("title") or entry.get("id") or "Untitled"
    pid = entry.get("id") or playlist_id_from_url(url)
    # Only real playlist entries (a list id) are useful for downloading.
    if not pid:
        return None
    if not url:
        if pid.startswith(("PL", "LL", "LM", "WL", "HL", "UU", "UL", "RD")):
            url = f"https://www.youtube.com/playlist?list={pid}"
        else:
            return None
    return Playlist(
        title=title,
        url=url,
        id=pid,
        kind=kind,
        entry_count=entry.get("playlist_count") or 0,
    )


def _collect_playlist_entries(info: dict[str, Any] | None, kind: str, out: dict[str, Playlist]) -> None:
    if not info:
        return
    for entry in info.get("entries") or []:
        pl = _entry_to_playlist(entry, kind)
        if pl and pl.id:
            out.setdefault(pl.id, pl)


def discover_playlists(cfg) -> list[Playlist]:
    """Return all playlists discoverable from the account (deduped)."""
    # --- Strategy 1: Google OAuth + YouTube Data API --------------------
    try:
        from .ytapi import YouTubeAPIError, api_for_config

        api = api_for_config(cfg)
        if api:
            playlists = api.list_my_playlists()
            if playlists:
                return sorted(playlists, key=lambda p: (p.kind != "discovered", p.title.lower()))
    except YouTubeAPIError:
        pass  # fall through to the scraping strategies below
    except Exception:
        pass

    found: dict[str, Playlist] = {}

    # --- Strategy 2: the account library feed ---------------------------
    library = _extract("https://www.youtube.com/feed/library", cfg)
    if library:
        for entry in library.get("entries") or []:
            url = (entry or {}).get("url") or ""
            if "/feed/playlists" in url:
                info = _extract(url, cfg)
                _collect_playlist_entries(info, "discovered", found)
            elif "list=" in url:
                pl = _entry_to_playlist(entry, "special")
                if pl and pl.id:
                    found.setdefault(pl.id, pl)

    # --- Strategy 2: the playlists feed directly -----------------------
    if not found:
        info = _extract("https://www.youtube.com/feed/playlists", cfg)
        _collect_playlist_entries(info, "discovered", found)

    if not found:
        raise DiscoveryError(
            "Could not discover any playlists from your YouTube account.\n"
            "YouTube may have changed its library endpoints. You can still add "
            "playlists manually by pasting their URL or ID."
        )

    playlists = sorted(found.values(), key=lambda p: (p.kind != "discovered", p.title.lower()))
    return playlists


def discover_channel_playlists(cfg, channel_url: str) -> list[Playlist]:
    """Return playlists from a channel/user's playlists tab."""
    if not channel_url.startswith("http"):
        channel_url = f"https://www.youtube.com/{channel_url}"
    if not channel_url.rstrip().endswith("/playlists"):
        channel_url = channel_url.rstrip("/") + "/playlists"
    info = _extract(channel_url, cfg)
    found: dict[str, Playlist] = {}
    _collect_playlist_entries(info, "channel", found)
    if not found:
        raise DiscoveryError(f"No playlists found at {channel_url}")
    return sorted(found.values(), key=lambda p: p.title.lower())


def dedupe(playlists: Iterable[Playlist]) -> list[Playlist]:
    seen: dict[str, Playlist] = {}
    for pl in playlists:
        key = pl.id or pl.url
        seen.setdefault(key, pl)
    return list(seen.values())
