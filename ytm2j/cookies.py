"""Cookie source discovery and validation.

yt-dlp can read cookies either directly from an installed browser
(``--cookies-from-browser``) or from a Netscape-format ``cookies.txt`` file.
This module figures out which browsers exist on the current machine and
which source is currently configured.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .utils import find_executable

# Browsers yt-dlp knows how to read cookies from, and how to find them on disk.
BROWSERS = {
    "chrome": {"names": ["chrome", "google-chrome", "google-chrome-stable", "chromium"], "label": "Chrome / Chromium"},
    "firefox": {"names": ["firefox"], "label": "Firefox"},
    "edge": {"names": ["microsoft-edge", "msedge"], "label": "Microsoft Edge"},
    "brave": {"names": ["brave-browser", "brave"], "label": "Brave"},
    "opera": {"names": ["opera"], "label": "Opera"},
    "safari": {"names": ["safari"], "label": "Safari (macOS only)"},
}


def _browser_installed(browser: str) -> bool:
    meta = BROWSERS.get(browser)
    if not meta:
        return False
    for name in meta["names"]:
        if find_executable(name):
            return True
    return False


def installed_browsers() -> list[str]:
    return [b for b in BROWSERS if _browser_installed(b)]


@dataclass
class CookieStatus:
    configured: bool
    method: str          # "browser" | "file" | "none"
    browser: str = ""
    browser_label: str = ""
    file: str = ""
    browsers_available: list[str] = None  # type: ignore[assignment]
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "configured": self.configured,
            "method": self.method,
            "browser": self.browser,
            "browser_label": self.browser_label,
            "file": self.file,
            "browsers_available": self.browsers_available or [],
            "notes": self.notes,
        }


def cookie_file_ok(path: str) -> bool:
    if not path:
        return False
    p = Path(path).expanduser()
    if not p.exists():
        return False
    try:
        text = p.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    # A Netscape cookie file must have the 7-field header.
    lines = [ln for ln in text.splitlines() if ln.strip() and not ln.startswith("#")]
    if not lines:
        return False
    return len(lines[0].split("\t")) >= 7


def status(cfg) -> CookieStatus:
    """Describe the effective cookie configuration for the current platform."""
    avail = installed_browsers()
    if cfg.cookie_browser:
        b = cfg.cookie_browser
        label = BROWSERS.get(b, {}).get("label", b)
        note = ""
        if b == "safari" and sys.platform != "darwin":
            note = "Safari cookies can only be read on macOS."
        if not _browser_installed(b) and not note:
            note = f"{label} was not found on this machine."
        return CookieStatus(
            configured=True,
            method="browser",
            browser=b,
            browser_label=label,
            browsers_available=avail,
            notes=note,
        )
    if cfg.cookie_file:
        ok = cookie_file_ok(cfg.cookie_file)
        return CookieStatus(
            configured=True,
            method="file",
            file=cfg.cookie_file,
            browsers_available=avail,
            notes="" if ok else f"Could not read a valid Netscape cookie file at {cfg.cookie_file}.",
        )
    return CookieStatus(
        configured=False,
        method="none",
        browsers_available=avail,
        notes="No cookie source configured. Set a browser or cookies.txt in Settings.",
    )


def build_cookie_opts(cfg) -> dict[str, Any]:
    """Translate config into the yt-dlp cookie options dict."""
    opts: dict[str, Any] = {}
    if cfg.cookie_browser:
        opts["cookiesfrombrowser"] = (cfg.cookie_browser,)
    if cfg.cookie_file:
        opts["cookiefile"] = str(Path(cfg.cookie_file).expanduser())
    return opts
