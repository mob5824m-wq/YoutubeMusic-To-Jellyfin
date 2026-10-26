"""FastAPI web server + JSON API.

Serves the single-page UI and exposes the controls the UI needs. Binds to
0.0.0.0 so it is reachable from other devices on the LAN as well as the
local machine.
"""

from __future__ import annotations

import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__
from .auth import AuthError, GoogleAuth
from .auth import status as auth_status
from .config import Config, load_config, platform_config_dir, save_config
from .cookies import BROWSERS, cookie_file_ok, installed_browsers, status as cookie_status
from .db import StateDB
from .discover import DiscoveryError, discover_channel_playlists, discover_playlists
from .models import JobStatus, normalize_playlist_url
from .runner import JobRunner
from .utils import ensure_dir, find_executable

STATIC_DIR = Path(__file__).parent / "static"


class ManualPlaylist(BaseModel):
    url: str = Field(..., description="Playlist URL, or a bare list ID / @handle")
    title: str = ""


class DownloadRequest(BaseModel):
    ids: list[str] | None = None


class ConfigUpdate(BaseModel):
    values: dict[str, Any]


def _app_state(app: FastAPI):
    return app.state


def create_app(cfg: Config | None = None, db: StateDB | None = None, runner: JobRunner | None = None) -> FastAPI:
    cfg = cfg or load_config()
    db = db or StateDB()
    if runner is None:
        runner = JobRunner(cfg, db)
    app = FastAPI(title="ytm2j", version=__version__, docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.state.cfg = cfg
    app.state.db = db
    app.state.runner = runner
    app.state.auth_flows: dict[str, dict[str, Any]] = {}
    app.state.auth_lock = threading.Lock()

    # ------------------------------------------------------------------ #
    # Static UI
    # ------------------------------------------------------------------ #
    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        page = STATIC_DIR / "app.html"
        if page.exists():
            return HTMLResponse(page.read_text(encoding="utf-8"))
        return HTMLResponse("<h1>ytm2j</h1><p>Static assets missing.</p>", status_code=200)

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        return {"ok": True, "version": __version__}

    # ------------------------------------------------------------------ #
    # Config
    # ------------------------------------------------------------------ #
    @app.get("/api/config")
    async def get_config() -> dict[str, Any]:
        c: Config = _app_state(app).cfg
        return {
            "config": c.to_dict(),
            "audio_formats": sorted(["mp3", "m4a", "opus", "vorbis", "flac", "wav"]),
            "browsers": [{"id": k, "label": v["label"]} for k, v in BROWSERS.items()],
            "platforms": {
                "config_dir": str(platform_config_dir()),
                "ffmpeg": c.resolved_ffmpeg() or "(not found)",
                "output_dir": str(c.resolved_output_dir()),
                "archive": c.resolved_archive(),
            },
        }

    @app.post("/api/config")
    async def update_config(payload: ConfigUpdate) -> dict[str, Any]:
        c: Config = _app_state(app).cfg
        try:
            c.update(payload.values)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        save_config(c)
        return {"ok": True, "config": c.to_dict()}

    # ------------------------------------------------------------------ #
    # Cookies
    # ------------------------------------------------------------------ #
    @app.get("/api/cookies")
    async def cookies() -> dict[str, Any]:
        return cookie_status(_app_state(app).cfg).to_dict()

    # ------------------------------------------------------------------ #
    # Google OAuth (YouTube Data API)
    # ------------------------------------------------------------------ #
    @app.get("/api/auth/status")
    async def auth_status_endpoint() -> dict[str, Any]:
        return auth_status(_app_state(app).cfg)

    @app.post("/api/auth/start")
    async def auth_start() -> dict[str, Any]:
        cfg = _app_state(app).cfg
        auth = GoogleAuth(cfg)
        try:
            flow = auth.create_flow(open_browser=False)
        except AuthError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        with _app_state(app).auth_lock:
            _app_state(app).auth_flows[flow["state"]] = flow
        return {
            "ok": True,
            "url": flow["url"],
            "redirect_uri": flow["redirect_uri"],
            "state": flow["state"],
        }

    @app.post("/api/auth/exchange")
    async def auth_exchange(payload: dict[str, Any]) -> dict[str, Any]:
        cfg = _app_state(app).cfg
        auth = GoogleAuth(cfg)
        state = payload.get("state") or ""
        code = (payload.get("code") or "").strip()
        url = (payload.get("url") or "").strip()
        if not code and url:
            from urllib.parse import parse_qs, urlparse

            q = parse_qs(urlparse(url).query)
            code = (q.get("code") or [""])[0]
            state = state or (q.get("state") or [""])[0]
        with _app_state(app).auth_lock:
            flow = _app_state(app).auth_flows.pop(state, None)
        if not flow:
            raise HTTPException(status_code=400, detail="No pending authorization. Press Connect again.")
        try:
            tokens = auth.exchange_code(code, flow["verifier"], flow["redirect_uri"])
        except AuthError as exc:
            flow["server"].stop()
            raise HTTPException(status_code=400, detail=str(exc))
        flow["server"].stop()
        return {"ok": True, "expires_in": int(max(0, tokens.expires_at - time.time()))}

    @app.get("/api/auth/callback")
    async def auth_callback(
        state: str = "",
        code: str = "",
        error: str = "",
        error_description: str = "",
    ) -> HTMLResponse:
        cfg = _app_state(app).cfg
        auth = GoogleAuth(cfg)
        if error:
            return HTMLResponse(
                f"<h2>Authorization failed</h2><p>{error_description or error}</p>", status_code=400
            )
        with _app_state(app).auth_lock:
            flow = _app_state(app).auth_flows.pop(state, None)
        if not flow:
            return HTMLResponse(
                "<h2>Unknown or expired authorization</h2><p>Start again from ytm2j.</p>", status_code=400
            )
        try:
            auth.exchange_code(code, flow["verifier"], flow["redirect_uri"])
        except AuthError as exc:
            return HTMLResponse(f"<h2>Authorization failed</h2><p>{exc}</p>", status_code=400)
        flow["server"].stop()
        return HTMLResponse("<h2>Connected to Google</h2><p>You can close this window.</p>")

    @app.post("/api/auth/logout")
    async def auth_logout() -> dict[str, Any]:
        GoogleAuth(_app_state(app).cfg).revoke()
        return {"ok": True}

    # ------------------------------------------------------------------ #
    # Playlists
    # ------------------------------------------------------------------ #
    @app.get("/api/playlists")
    async def list_playlists() -> dict[str, Any]:
        return {"playlists": [p.to_dict() for p in _app_state(app).db.list_playlists()]}

    @app.post("/api/playlists/manual")
    async def add_playlist(payload: ManualPlaylist) -> dict[str, Any]:
        from .models import Playlist

        url = normalize_playlist_url(payload.url)
        if not url:
            raise HTTPException(status_code=400, detail="A playlist URL or ID is required")
        title = payload.title or url.split("list=")[-1] or url
        pl = Playlist(title=title, url=url, kind="manual")
        _app_state(app).db.add_playlist(pl)
        return {"ok": True, "playlist": pl.to_dict()}

    @app.delete("/api/playlists/{playlist_id}")
    async def remove_playlist(playlist_id: str) -> dict[str, Any]:
        _app_state(app).db.remove_playlist(playlist_id)
        return {"ok": True}

    @app.post("/api/discover")
    async def discover(url: str | None = None) -> dict[str, Any]:
        c: Config = _app_state(app).cfg
        try:
            if url:
                pls = discover_channel_playlists(c, url)
            else:
                pls = discover_playlists(c)
        except DiscoveryError as exc:
            raise HTTPException(status_code=502, detail=str(exc))
        _app_state(app).db.save_playlists(pls)
        return {"ok": True, "count": len(pls), "playlists": [p.to_dict() for p in pls]}

    # ------------------------------------------------------------------ #
    # Downloads / jobs
    # ------------------------------------------------------------------ #
    @app.get("/api/jobs")
    async def list_jobs() -> dict[str, Any]:
        jobs = [j.to_summary() for j in _app_state(app).db.list_jobs(limit=300)]
        return {"jobs": jobs, "active": _app_state(app).runner.is_busy()}

    @app.get("/api/jobs/{job_id}")
    async def get_job(job_id: str) -> dict[str, Any]:
        job = _app_state(app).db.get_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="job not found")
        return {"job": job.to_dict()}

    @app.post("/api/download")
    async def download(payload: DownloadRequest) -> dict[str, Any]:
        runner: JobRunner = _app_state(app).runner
        db = _app_state(app).db
        if payload.ids:
            wanted = set(payload.ids)
            playlists = [p for p in db.list_playlists() if p.id in wanted or p.url in wanted]
            if not playlists:
                raise HTTPException(status_code=404, detail="no matching playlists")
        else:
            playlists = db.list_playlists()
        if not playlists:
            raise HTTPException(status_code=400, detail="No playlists to download")
        jobs = runner.enqueue_many(playlists)
        return {"ok": True, "queued": len(jobs), "jobs": [j.to_summary() for j in jobs]}

    @app.post("/api/download/{playlist_id}")
    async def download_one(playlist_id: str) -> dict[str, Any]:
        runner: JobRunner = _app_state(app).runner
        db = _app_state(app).db
        match = next((p for p in db.list_playlists() if p.id == playlist_id), None)
        if not match:
            raise HTTPException(status_code=404, detail="playlist not found")
        job = runner.enqueue(match)
        return {"ok": True, "job": job.to_summary()}

    @app.post("/api/jobs/{job_id}/cancel")
    async def cancel_job(job_id: str) -> dict[str, Any]:
        ok = _app_state(app).runner.cancel(job_id)
        return {"ok": ok}

    @app.delete("/api/jobs/{job_id}")
    async def delete_job(job_id: str) -> dict[str, Any]:
        _app_state(app).runner.remove(job_id)
        return {"ok": True}

    @app.post("/api/jobs/clear")
    async def clear_jobs() -> dict[str, Any]:
        n = _app_state(app).db.clear_finished()
        return {"ok": True, "removed": n}

    @app.get("/api/logs")
    async def logs(limit: int = 200) -> dict[str, Any]:
        return {"logs": _app_state(app).db.list_logs(limit)}

    # ------------------------------------------------------------------ #
    # Diagnostics
    # ------------------------------------------------------------------ #
    @app.post("/api/doctor")
    async def doctor() -> dict[str, Any]:
        c: Config = _app_state(app).cfg
        checks: dict[str, Any] = {}

        # yt-dlp importable?
        try:
            import yt_dlp  # noqa: F401
            checks["yt_dlp"] = {"ok": True, "version": __import__("yt_dlp").version.__version__}
        except Exception as exc:
            checks["yt_dlp"] = {"ok": False, "error": str(exc)}

        # ffmpeg present & runnable?
        ff = c.resolved_ffmpeg()
        if not ff:
            ff = find_executable("ffmpeg", "ffmpeg.exe") or ""
        checks["ffmpeg"] = {"ok": False, "path": ff}
        if ff:
            try:
                proc = subprocess.run(
                    [ff, "-version"], capture_output=True, text=True, timeout=15
                )
                checks["ffmpeg"] = {
                    "ok": proc.returncode == 0,
                    "path": ff,
                    "version": proc.stdout.splitlines()[0] if proc.stdout else "",
                }
            except Exception as exc:
                checks["ffmpeg"] = {"ok": False, "path": ff, "error": str(exc)}

        # cookie source
        checks["cookies"] = cookie_status(c).to_dict()

        # Google OAuth (YouTube Data API)
        checks["google_oauth"] = auth_status(c)

        # writable output dir
        out = c.resolved_output_dir()
        try:
            ensure_dir(out)
            probe = out / ".ytm2j-write-test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
            checks["output_dir"] = {"ok": True, "path": str(out)}
        except Exception as exc:
            checks["output_dir"] = {"ok": False, "path": str(out), "error": str(exc)}

        # config dir
        cfg_dir = platform_config_dir()
        try:
            ensure_dir(cfg_dir)
            checks["config_dir"] = {"ok": True, "path": str(cfg_dir)}
        except Exception as exc:
            checks["config_dir"] = {"ok": False, "path": str(cfg_dir), "error": str(exc)}

        checks["browsers_available"] = installed_browsers()
        checks["problems"] = c.validate()
        return checks

    return app


def run(host: str = "0.0.0.0", port: int = 8765) -> None:
    import uvicorn

    app = create_app()
    app.state.runner.start()
    try:
        uvicorn.run(app, host=host, port=port, log_level="info")
    finally:
        app.state.runner.stop()
