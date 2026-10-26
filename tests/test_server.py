from __future__ import annotations

from fastapi.testclient import TestClient

from ytm2j.config import Config
from ytm2j.db import StateDB
from ytm2j.server import create_app


def make_client(tmp_path):
    cfg = Config()
    cfg.output_dir = str(tmp_path / "music")
    cfg.download_archive = str(tmp_path / "archive.txt")
    db = StateDB(str(tmp_path / "test.db"))
    app = create_app(cfg, db)
    return TestClient(app), db, cfg


def test_index_served(tmp_path):
    client, _, _ = make_client(tmp_path)
    r = client.get("/")
    assert r.status_code == 200
    assert "ytm2j" in r.text


def test_health(tmp_path):
    client, _, _ = make_client(tmp_path)
    r = client.get("/api/health")
    assert r.json()["ok"] is True


def test_config_get_and_set(tmp_path):
    client, _, cfg = make_client(tmp_path)
    r = client.get("/api/config")
    body = r.json()
    assert body["config"]["audio_format"] == "mp3"
    assert "mp3" in body["audio_formats"]
    assert "browsers" in body

    r = client.post("/api/config", json={"values": {"audio_format": "m4a", "audio_quality": "256"}})
    assert r.status_code == 200
    assert r.json()["config"]["audio_format"] == "m4a"
    assert cfg.audio_format == "m4a"

    r = client.post("/api/config", json={"values": {"audio_format": "bogus"}})
    assert r.status_code == 400


def test_cookies_endpoint(tmp_path):
    client, _, _ = make_client(tmp_path)
    r = client.get("/api/cookies")
    body = r.json()
    assert body["configured"] is False
    assert "browsers_available" in body


def test_playlist_manual_add_list_delete(tmp_path):
    client, _, _ = make_client(tmp_path)
    r = client.post("/api/playlists/manual", json={"url": "PLabcdefghijk"})
    assert r.status_code == 200
    pid = r.json()["playlist"]["id"]
    assert pid == "PLabcdefghijk"

    r = client.get("/api/playlists")
    assert len(r.json()["playlists"]) == 1

    r = client.delete(f"/api/playlists/{pid}")
    assert r.status_code == 200
    assert client.get("/api/playlists").json()["playlists"] == []


def test_playlist_manual_rejects_empty(tmp_path):
    client, _, _ = make_client(tmp_path)
    r = client.post("/api/playlists/manual", json={"url": "   "})
    assert r.status_code == 400


def test_download_enqueues_jobs(tmp_path):
    client, _, _ = make_client(tmp_path)
    client.post("/api/playlists/manual", json={"url": "PL11111111111"})
    client.post("/api/playlists/manual", json={"url": "PL22222222222"})

    r = client.post("/api/download", json={"ids": ["PL11111111111"]})
    assert r.status_code == 200
    assert r.json()["queued"] == 1

    r = client.get("/api/jobs")
    jobs = r.json()["jobs"]
    assert len(jobs) == 1
    assert jobs[0]["url"].endswith("list=PL11111111111")
    assert jobs[0]["status"] == "queued"


def test_download_all_requires_playlists(tmp_path):
    client, _, _ = make_client(tmp_path)
    r = client.post("/api/download", json={})
    assert r.status_code == 400


def test_job_cancel_and_remove(tmp_path):
    client, _, _ = make_client(tmp_path)
    client.post("/api/playlists/manual", json={"url": "PL33333333333"})
    job_id = client.post("/api/download", json={}).json()["jobs"][0]["id"]

    r = client.post(f"/api/jobs/{job_id}/cancel")
    assert r.status_code == 200

    r = client.delete(f"/api/jobs/{job_id}")
    assert r.status_code == 200
    assert client.get("/api/jobs").json()["jobs"] == []


def test_clear_finished(tmp_path):
    client, db, _ = make_client(tmp_path)
    client.post("/api/playlists/manual", json={"url": "PL44444444444"})
    client.post("/api/download", json={})
    db.update_job(client.get("/api/jobs").json()["jobs"][0]["id"], status="done")
    r = client.post("/api/jobs/clear")
    assert r.json()["removed"] >= 1
    assert client.get("/api/jobs").json()["jobs"] == []


def test_doctor(tmp_path):
    client, _, _ = make_client(tmp_path)
    r = client.post("/api/doctor")
    assert r.status_code == 200
    body = r.json()
    assert body["yt_dlp"]["ok"] is True
    assert body["ffmpeg"]["ok"] is True
    assert body["output_dir"]["ok"] is True
    assert "problems" in body
