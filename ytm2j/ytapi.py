"""YouTube Data API v3 client.

Used for the part Google OAuth *can* do well: enumerating the account's
playlists and their tracks. Downloads themselves still go through yt-dlp
(which needs cookies, not a Data API token).
"""

from __future__ import annotations

import json
from typing import Any, Iterator
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .models import Playlist, Track

BASE = "https://www.googleapis.com/youtube/v3"


class YouTubeAPIError(Exception):
    pass


class YouTubeAPI:
    def __init__(self, access_token: str, timeout: int = 30):
        self.access_token = access_token
        self.timeout = timeout

    # ------------------------------------------------------------------ #
    def _request(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        params = dict(params)
        params.setdefault("maxResults", "50")
        url = f"{BASE}/{path}?" + urlencode(params)
        req = Request(
            url,
            headers={
                "Authorization": f"Bearer {self.access_token}",
                "Accept": "application/json",
            },
        )
        try:
            with urlopen(req, timeout=self.timeout) as resp:
                return json.load(resp)
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", "ignore")[:400]
            raise YouTubeAPIError(f"YouTube API {path} failed ({exc.code}): {detail}") from exc
        except (URLError, OSError) as exc:
            raise YouTubeAPIError(f"Could not reach YouTube Data API: {exc}") from exc

    def _pages(self, path: str, params: dict[str, Any]) -> Iterator[dict[str, Any]]:
        page_token = None
        while True:
            p = dict(params)
            if page_token:
                p["pageToken"] = page_token
            data = self._request(path, p)
            yield data
            page_token = data.get("nextPageToken")
            if not page_token:
                return

    # ------------------------------------------------------------------ #
    def related_playlists(self) -> dict[str, str]:
        """Channel relatedPlaylists (uploads, watchLater, likes, history)."""
        data = self._request("channels", {"mine": "true", "part": "contentDetails"})
        for item in data.get("items") or []:
            rp = (item.get("contentDetails") or {}).get("relatedPlaylists") or {}
            if rp:
                return {k: v for k, v in rp.items() if v}
        return {}

    def list_my_playlists(self) -> list[Playlist]:
        """Every playlist in the account: special + user-created."""
        rp = self.related_playlists()

        specials: dict[str, str] = {}
        for key, label in (("likes", "Liked Videos"), ("watchLater", "Watch Later")):
            if rp.get(key):
                specials[rp[key]] = label
        # Well-known YouTube / YouTube Music ids.
        for pid, label in (("LM", "Liked Songs"), ("LL", "Liked Videos"), ("WL", "Watch Later")):
            specials.setdefault(pid, label)

        playlists: list[Playlist] = [
            Playlist(title=label, url=f"https://www.youtube.com/playlist?list={pid}", id=pid, kind="special")
            for pid, label in specials.items()
        ]

        for data in self._pages("playlists", {"mine": "true", "part": "snippet,contentDetails"}):
            for item in data.get("items") or []:
                pid = item.get("id")
                if not pid:
                    continue
                snippet = item.get("snippet") or {}
                count = (item.get("contentDetails") or {}).get("itemCount") or 0
                playlists.append(
                    Playlist(
                        title=snippet.get("title") or pid,
                        url=f"https://www.youtube.com/playlist?list={pid}",
                        id=pid,
                        kind="discovered",
                        entry_count=count,
                    )
                )
        return playlists

    def list_playlist_items(self, playlist_id: str) -> list[Track]:
        """All tracks in a playlist, in order."""
        tracks: list[Track] = []
        index = 0
        for data in self._pages(
            "playlistItems", {"playlistId": playlist_id, "part": "snippet,contentDetails"}
        ):
            for item in data.get("items") or []:
                index += 1
                snippet = item.get("snippet") or {}
                vid = (snippet.get("resourceId") or {}).get("videoId") or ""
                tracks.append(
                    Track(
                        index=index,
                        title=snippet.get("title") or "",
                        artist=snippet.get("videoOwnerChannelTitle") or "",
                        url=f"https://www.youtube.com/watch?v={vid}" if vid else "",
                        album="",
                    )
                )
        return tracks


def api_for_config(cfg) -> "YouTubeAPI | None":
    """Build an API client if Google OAuth is configured *and* authorized."""
    from .auth import GoogleAuth

    token = GoogleAuth(cfg).get_valid_access_token()
    return YouTubeAPI(token) if token else None
