from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture
def tmp_config(tmp_path, monkeypatch):
    """A Config whose output/archive paths point inside tmp_path."""
    from ytm2j.config import Config

    cfg = Config()
    cfg.output_dir = str(tmp_path / "music")
    cfg.download_archive = str(tmp_path / "archive.txt")
    cfg.cookie_browser = ""
    cfg.cookie_file = ""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    return cfg
