"""Small, dependency-free helpers used across ytm2j."""

from __future__ import annotations

import re
import shutil
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

# Characters that are illegal in folder names on Windows / macOS / Linux.
_ILLEGAL_FS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
# Windows reserves a handful of names even with extensions.
_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:$")


def sanitize_folder(name: str, fallback: str = "Untitled") -> str:
    """Return a safe, human-readable folder name.

    Strips path separators and platform-illegal characters, collapses
    whitespace, truncates to a sane length and guarantees a non-empty result.
    """
    if not name:
        return fallback
    # Normalise unicode + trim.
    name = unicodedata.normalize("NFC", str(name)).strip()
    # Replace illegal characters with spaces.
    name = _ILLEGAL_FS.sub(" ", name)
    # Collapse runs of whitespace/dots (leading dots would make a hidden file).
    name = re.sub(r"[\s.]+", " ", name).strip(" .")
    # Truncate so that a long title still leaves room for the extension.
    if len(name) > 120:
        name = name[:120].rstrip(" .")
    if not name:
        return fallback
    if name.upper() in _RESERVED or _WINDOWS_DRIVE.match(name):
        name = f"_{name}"
    return name


def ensure_dir(path: str | Path) -> Path:
    p = Path(path).expanduser()
    p.mkdir(parents=True, exist_ok=True)
    return p


def safe_join(base: str | Path, *parts: str) -> Path:
    """Join ``parts`` onto ``base`` while refusing to escape the base folder."""
    base_p = Path(base).expanduser().resolve()
    target = base_p.joinpath(*[sanitize_folder(p) for p in parts if p])
    # Defence in depth: never allow a resolved path to climb out of ``base``.
    try:
        target.relative_to(base_p)
    except ValueError:
        target = base_p / sanitize_folder("Untitled")
    return target


def format_bytes(value: int | float | None) -> str:
    if value is None:
        return "?"
    units = ("B", "KB", "MB", "GB", "TB")
    size = float(value)
    for unit in units:
        if abs(size) < 1024.0 or unit == units[-1]:
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024.0
    return f"{size:.1f} TB"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def find_executable(*names: str) -> str | None:
    """Locate an executable on PATH (cross-platform)."""
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return None
