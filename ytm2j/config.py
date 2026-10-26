"""Configuration handling for ytm2j.

Settings live in a JSON file under the user's config dir:

    Windows:  %APPDATA%\\ytm2j\\config.json
    macOS:    ~/Library/Application Support/ytm2j/config.json
    Linux:    ~/.config/ytm2j/config.json   (XDG_CONFIG_HOME honoured)

CLI flags and environment variables override the saved file.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

AUDIO_FORMATS = {"mp3", "m4a", "opus", "vorbis", "flac", "wav"}
# Formats that are natively produced by YouTube without re-encoding to mp3.
_NATIVE_CONTAINER_FORMATS = {"m4a", "webm"}


def platform_config_dir() -> Path:
    """Return the per-user config directory for the current platform."""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "ytm2j"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "ytm2j"
    # Linux / *BSD - honour XDG_CONFIG_HOME when set.
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "ytm2j"


@dataclass
class Config:
    # --- Where music lands ---
    output_dir: str = ""
    # --- Audio ---
    audio_format: str = "mp3"
    audio_quality: str = "320"
    embed_thumbnail: bool = True
    add_metadata: bool = True
    # --- Authentication ---
    cookie_browser: str = ""     # chrome / firefox / edge / brave / chromium / safari / opera
    cookie_file: str = ""        # Netscape cookies.txt path
    # Google OAuth (YouTube Data API) - used for account discovery/track lists.
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_port: int = 8766
    # --- Tooling ---
    ffmpeg_location: str = ""    # empty -> auto-detect (bundled imageio-ffmpeg, then PATH)
    yt_dlp_path: str = ""        # empty -> use the bundled library
    player_client: str = ""      # youtube extractor player_client, e.g. web_music, default
    # --- Download behaviour ---
    download_archive: str = ""   # yt-dlp archive file -> skip already-downloaded tracks
    concurrent: int = 1          # how many playlists download at once
    playlist_folder_template: str = ""  # empty -> use playlist title
    include_special: bool = True
    dry_run: bool = False

    # ------------------------------------------------------------------ #
    def resolved_output_dir(self) -> Path:
        raw = self.output_dir or str(Path.home() / "Music" / "YouTube Music")
        return Path(raw).expanduser()

    def resolved_ffmpeg(self) -> str:
        if self.ffmpeg_location:
            return self.ffmpeg_location
        try:  # optional dependency, only present when the `ffmpeg` extra is installed
            import imageio_ffmpeg  # type: ignore
            return imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            return ""

    def resolved_archive(self) -> str:
        if self.download_archive:
            return self.download_archive
        return str(platform_config_dir() / "download_archive.txt")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def update(self, values: dict[str, Any]) -> "Config":
        valid = set(asdict(self).keys())
        for key, value in values.items():
            if key not in valid or value is None:
                continue
            if key == "audio_format" and value:
                value = str(value).lower()
                if value not in AUDIO_FORMATS:
                    raise ValueError(f"audio_format must be one of {sorted(AUDIO_FORMATS)}")
            if key == "concurrent":
                value = max(1, int(value))
            setattr(self, key, value)
        return self

    def validate(self) -> list[str]:
        """Return a list of human-readable problems (empty == all good)."""
        problems: list[str] = []
        if self.audio_format not in AUDIO_FORMATS:
            problems.append(f"audio_format '{self.audio_format}' is not supported")
        if self.concurrent < 1:
            problems.append("concurrent must be >= 1")
        if self.cookie_browser and self.cookie_file:
            problems.append("set either cookie_browser or cookie_file, not both")
        if not self.cookie_browser and not self.cookie_file:
            problems.append(
                "no cookies configured - private playlists need auth. "
                "Set 'cookie_browser' or 'cookie_file'."
            )
        return problems


def default_config() -> Config:
    return Config()


def load_config(path: str | Path | None = None) -> Config:
    cfg = default_config()
    cfg_path = Path(path) if path else (platform_config_dir() / "config.json")
    if cfg_path.exists():
        try:
            data = json.loads(cfg_path.read_text(encoding="utf-8"))
            cfg.update(data)
        except (json.JSONDecodeError, OSError) as exc:
            # A corrupt config should never brick the app - warn and continue.
            print(f"[ytm2j] warning: could not read {cfg_path}: {exc}", file=sys.stderr)
    # Environment overrides (handy for containers / CI).
    if os.environ.get("YTM2J_OUTPUT_DIR"):
        cfg.output_dir = os.environ["YTM2J_OUTPUT_DIR"]
    if os.environ.get("YTM2J_AUDIO_FORMAT"):
        cfg.audio_format = os.environ["YTM2J_AUDIO_FORMAT"].lower()
    if os.environ.get("YTM2J_COOKIE_BROWSER"):
        cfg.cookie_browser = os.environ["YTM2J_COOKIE_BROWSER"]
    if os.environ.get("YTM2J_COOKIE_FILE"):
        cfg.cookie_file = os.environ["YTM2J_COOKIE_FILE"]
    if os.environ.get("GOOGLE_CLIENT_ID"):
        cfg.google_client_id = os.environ["GOOGLE_CLIENT_ID"]
    if os.environ.get("GOOGLE_CLIENT_SECRET"):
        cfg.google_client_secret = os.environ["GOOGLE_CLIENT_SECRET"]
    return cfg


def save_config(cfg: Config, path: str | Path | None = None) -> Path:
    cfg_path = Path(path) if path else (platform_config_dir() / "config.json")
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(json.dumps(cfg.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    return cfg_path
