from __future__ import annotations

from pathlib import Path

import pytest

from ytm2j.config import Config, load_config, save_config
from ytm2j.cookies import cookie_file_ok
from ytm2j.discover import _collect_playlist_entries, _entry_to_playlist
from ytm2j.downloader import PlaylistDownloader
from ytm2j.models import Job, JobStatus, Playlist, normalize_playlist_url, playlist_id_from_url
from ytm2j.utils import format_bytes, sanitize_folder, safe_join


# --------------------------------------------------------------------- utils
class TestSanitize:
    @pytest.mark.parametrize("raw,expected", [
        ("Rock/Pop", "Rock Pop"),
        ('a:b*c?d"e<f>g|h', "a b c d e f g h"),
        ("   leading spaces   ", "leading spaces"),
        ("", "Untitled"),
        ("   ", "Untitled"),
        ("CON", "_CON"),
        ("C:", "C"),
        ("a" * 200, "a" * 120),
        ("dots...name..", "dots name"),
    ])
    def test_sanitize_folder(self, raw, expected):
        assert sanitize_folder(raw) == expected

    def test_safe_join_cannot_escape(self, tmp_path):
        base = tmp_path / "base"
        base.mkdir()
        out = safe_join(base, "..", "..", "etc", "passwd")
        assert str(out).startswith(str(base.resolve()))

    def test_format_bytes(self):
        assert format_bytes(0) == "0 B"
        assert format_bytes(1024) == "1.0 KB"
        assert format_bytes(1048576) == "1.0 MB"
        assert format_bytes(None) == "?"


# -------------------------------------------------------------------- models
class TestModels:
    def test_normalize_bare_id(self):
        assert normalize_playlist_url("PLabcdefghijk") == "https://www.youtube.com/playlist?list=PLabcdefghijk"

    def test_normalize_music_url_passthrough(self):
        u = "https://music.youtube.com/playlist?list=LM"
        assert normalize_playlist_url(u) == u

    def test_normalize_handle(self):
        assert normalize_playlist_url("@SomeChannel") == "https://www.youtube.com/@SomeChannel/playlists"

    def test_playlist_id_from_url(self):
        assert playlist_id_from_url("https://www.youtube.com/playlist?list=PLxyz") == "PLxyz"
        assert playlist_id_from_url("https://youtu.be/abc") == ""

    def test_playlist_folder_defaults_to_title(self):
        pl = Playlist(title="My Awesome Mix", url="https://www.youtube.com/playlist?list=PL1")
        assert pl.folder == "My Awesome Mix"
        assert pl.is_special is False

    def test_job_progress(self):
        job = Job(id="j", url="u", title="t", folder="f", total=4, done=2)
        assert job.progress() == 0.5
        assert job.to_summary()["status"] == JobStatus.QUEUED.value


# -------------------------------------------------------------------- config
class TestConfig:
    def test_roundtrip(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
        cfg = Config()
        cfg.audio_format = "m4a"
        cfg.cookie_browser = "firefox"
        path = save_config(cfg)
        loaded = load_config(path)
        assert loaded.audio_format == "m4a"
        assert loaded.cookie_browser == "firefox"

    def test_env_override(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
        monkeypatch.setenv("YTM2J_OUTPUT_DIR", str(tmp_path / "from-env"))
        cfg = load_config()
        assert cfg.output_dir == str(tmp_path / "from-env")

    def test_invalid_format_rejected(self):
        cfg = Config()
        with pytest.raises(ValueError):
            cfg.update({"audio_format": "not-a-format"})

    def test_validate_flags_missing_cookies(self):
        cfg = Config()
        assert any("cookies" in p for p in cfg.validate())
        cfg.cookie_browser = "chrome"
        assert cfg.validate() == []


# ------------------------------------------------------------------- cookies
class TestCookies:
    def test_cookie_file_ok(self, tmp_path):
        good = tmp_path / "cookies.txt"
        good.write_text(
            "# Netscape HTTP Cookie File\n"
            ".youtube.com\tTRUE\t/\tTRUE\t1\tCONSENT\tPENDING+deadbeef\n"
        )
        assert cookie_file_ok(str(good)) is True

        bad = tmp_path / "bad.txt"
        bad.write_text("not\ta\tcookie\nfile\n")
        assert cookie_file_ok(str(bad)) is False

        assert cookie_file_ok(str(tmp_path / "missing.txt")) is False


# ------------------------------------------------------------------- discover
class TestDiscoverHelpers:
    def test_entry_to_playlist_from_url(self):
        entry = {"url": "https://www.youtube.com/playlist?list=PLabc", "title": "My Mix"}
        pl = _entry_to_playlist(entry, "discovered")
        assert pl.id == "PLabc"
        assert pl.title == "My Mix"

    def test_entry_to_playlist_from_id(self):
        pl = _entry_to_playlist({"id": "PLabc", "title": "X"}, "discovered")
        assert pl.url == "https://www.youtube.com/playlist?list=PLabc"

    def test_entry_to_playlist_skips_non_playlist(self):
        assert _entry_to_playlist({"url": "https://youtu.be/abc", "title": "v"}, "discovered") is None

    def test_collect_dedupes(self):
        out: dict = {}
        info = {"entries": [
            {"url": "https://www.youtube.com/playlist?list=PL1", "title": "A"},
            {"url": "https://www.youtube.com/playlist?list=PL1", "title": "A again"},
            {"url": "https://www.youtube.com/playlist?list=PL2", "title": "B"},
        ]}
        _collect_playlist_entries(info, "discovered", out)
        assert len(out) == 2


# ----------------------------------------------------------------- downloader
class TestDownloaderOpts:
    def _dl(self, tmp_config):
        return PlaylistDownloader(tmp_config)

    def test_opts_contain_extractor_and_postprocessors(self, tmp_config):
        tmp_config.audio_format = "mp3"
        tmp_config.audio_quality = "320"
        opts = self._dl(tmp_config).build_opts("https://www.youtube.com/playlist?list=PL1", "My Mix")
        assert "FFmpegExtractAudio" in [pp["key"] for pp in opts["postprocessors"]]
        pp = opts["postprocessors"][0]
        assert pp["preferredcodec"] == "mp3"
        assert pp["preferredquality"] == "320"
        assert opts["format"] == "bestaudio/best"
        assert "My Mix" in opts["outtmpl"]

    def test_opts_embed_art_and_metadata(self, tmp_config):
        opts = self._dl(tmp_config).build_opts("u", "f")
        keys = [pp["key"] for pp in opts["postprocessors"]]
        assert "EmbedThumbnail" in keys
        assert "FFmpegMetadata" in keys
        assert opts["writethumbnail"] is True

    def test_opts_m4a_uses_mp4_container(self, tmp_config):
        tmp_config.audio_format = "m4a"
        opts = self._dl(tmp_config).build_opts("u", "f")
        assert opts["merge_output_format"] == "mp4"

    def test_opts_cookie_sources(self, tmp_config):
        tmp_config.cookie_browser = "firefox"
        opts = self._dl(tmp_config).build_opts("u", "f")
        assert opts["cookiesfrombrowser"] == ("firefox",)

        tmp_config.cookie_browser = ""
        tmp_config.cookie_file = "/tmp/cookies.txt"
        opts = self._dl(tmp_config).build_opts("u", "f")
        assert opts["cookiefile"] == "/tmp/cookies.txt"

    def test_opts_archive_and_ffmpeg(self, tmp_config):
        opts = self._dl(tmp_config).build_opts("u", "f")
        assert Path(opts["download_archive"]).name == "archive.txt"
        assert "ffmpeg_location" in opts and opts["ffmpeg_location"]

    def test_opts_skip_download_does_not_archive(self, tmp_config):
        opts = self._dl(tmp_config).build_opts("u", "f", skip_download=True)
        assert opts["skip_download"] is True
        assert "download_archive" not in opts

    def test_folder_for_template(self, tmp_config):
        tmp_config.playlist_folder_template = "%(playlist_title)s [%(playlist_id)s]"
        dl = self._dl(tmp_config)
        pl = Playlist(title="Road Trip", url="https://www.youtube.com/playlist?list=PLRoad", kind="discovered")
        assert dl.folder_for(pl) == "Road Trip [PLRoad]"

    def test_output_folder_uses_output_dir(self, tmp_config):
        folder = self._dl(tmp_config).output_folder("My Mix")
        assert folder.name == "My Mix"
        assert folder.parent == Path(tmp_config.output_dir).expanduser()
