"""Command line interface for ytm2j.

Examples
--------
    ytm2j doctor
    ytm2j discover
    ytm2j download --all
    ytm2j download "https://music.youtube.com/playlist?list=PL..." --format m4a
    ytm2j serve --port 8765
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from . import __version__
from .auth import status as auth_status
from .config import AUDIO_FORMATS, Config, load_config, save_config
from .cookies import installed_browsers, status as cookie_status
from .db import StateDB
from .discover import DiscoveryError, discover_channel_playlists, discover_playlists
from .downloader import PlaylistDownloader
from .models import JobStatus, normalize_playlist_url, Playlist
from .runner import JobRunner
from .utils import format_bytes


def _print(*args, **kwargs) -> None:
    kwargs.setdefault("file", sys.stdout)
    print(*args, **kwargs)


def _err(*args, **kwargs) -> None:
    kwargs.setdefault("file", sys.stderr)
    print(*args, **kwargs)


def _apply_overrides(cfg: Config, args) -> None:
    if getattr(args, "format", None):
        cfg.audio_format = args.format.lower()
    if getattr(args, "quality", None):
        cfg.audio_quality = args.quality
    if getattr(args, "output", None):
        cfg.output_dir = args.output
    if getattr(args, "browser", None):
        cfg.cookie_browser = args.browser
    if getattr(args, "cookies", None):
        cfg.cookie_file = args.cookies
    if getattr(args, "ffmpeg", None):
        cfg.ffmpeg_location = args.ffmpeg
    if getattr(args, "no_art", False):
        cfg.embed_thumbnail = False
    if getattr(args, "dry_run", False):
        cfg.dry_run = True


def cmd_version(_args) -> int:
    _print(f"ytm2j {__version__}")
    return 0


def cmd_doctor(_args) -> int:
    cfg = load_config()
    checks = {}
    try:
        import yt_dlp
        checks["yt-dlp"] = f"{yt_dlp.version.__version__} (ok)"
    except Exception as exc:
        checks["yt-dlp"] = f"NOT INSTALLED ({exc})"

    ff = cfg.resolved_ffmpeg()
    checks["ffmpeg"] = ff if ff else "NOT FOUND (install the 'ffmpeg' extra or ffmpeg system-wide)"
    checks["cookies"] = cookie_status(cfg).to_dict()
    checks["google_oauth"] = auth_status(cfg)
    checks["output_dir"] = str(cfg.resolved_output_dir())
    checks["browsers"] = installed_browsers() or ["(none detected)"]
    problems = cfg.validate()

    _print(f"ytm2j doctor - v{__version__}\n")
    for key, value in checks.items():
        if isinstance(value, dict):
            _print(f"  {key:<14}: {json.dumps(value)}")
        else:
            _print(f"  {key:<14}: {value}")
    _print()
    if problems:
        _print("Problems:")
        for p in problems:
            _print(f"  - {p}")
    else:
        _print("Everything looks good.")
    return 0 if not problems else 1


def cmd_config(args) -> int:
    cfg = load_config()
    if args.action == "show":
        _print(json.dumps(cfg.to_dict(), indent=2, ensure_ascii=False))
        return 0
    if args.action == "path":
        from .config import platform_config_dir

        _print(str(platform_config_dir()))
        return 0
    if args.action == "set":
        _apply_overrides(cfg, args)
        cfg.update({args.key: args.value})
        save_config(cfg)
        _print(f"{args.key} = {getattr(cfg, args.key)}")
        return 0
    _err(f"unknown config action: {args.action}")
    return 2


def cmd_discover(args) -> int:
    cfg = load_config()
    try:
        playlists = discover_channel_playlists(cfg, args.url) if args.url else discover_playlists(cfg)
    except DiscoveryError as exc:
        _err(str(exc))
        return 1
    for pl in playlists:
        _print(f"{pl.kind:<10} {pl.title}  [{pl.id}]  ({pl.url})")
    if args.json:
        _print(json.dumps([p.to_dict() for p in playlists], ensure_ascii=False, indent=2))
    _print(f"\n{len(playlists)} playlist(s) found")
    return 0


AUTH_SETUP_GUIDE = """\
Connect ytm2j to your YouTube account with Google OAuth.

1. Open  https://console.cloud.google.com/apis/credentials
2. Create an OAuth client ID, application type: "Desktop app".
3. Download the JSON and give it to ytm2j:

     ytm2j auth setup --credentials client_secret.json

   or set the values manually:

     ytm2j config set google_client_id    <id>.apps.googleusercontent.com
     ytm2j config set google_client_secret <secret>

   (the env vars GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET work too)

4. Start the login flow:

     ytm2j auth login

   It opens Google in your browser. Approve, and ytm2j stores a refresh
   token so it can list your playlists through the YouTube Data API.

Note: YouTube no longer lets yt-dlp *download* with an OAuth token, so for
fetching private tracks you may still need cookies (`ytm2j doctor` shows
whether cookies are configured). Google OAuth is used to connect to the
account and list playlists/tracks.
"""


def cmd_auth(args) -> int:
    from .auth import AuthError, GoogleAuth, load_credentials_file

    cfg = load_config()
    if args.action == "status":
        _print(json.dumps(auth_status(cfg), indent=2, ensure_ascii=False))
        return 0 if auth_status(cfg).get("connected") else 1
    if args.action == "setup":
        _print(AUTH_SETUP_GUIDE)
        if args.credentials:
            try:
                creds = load_credentials_file(args.credentials)
            except AuthError as exc:
                _err(str(exc))
                return 1
            cfg.update(creds)
            save_config(cfg)
            _print(f"\nLoaded credentials from {args.credentials}")
            _print("Now run:  ytm2j auth login")
        return 0
    if args.action == "login":
        if args.credentials:
            creds = load_credentials_file(args.credentials)
            cfg.update(creds)
            save_config(cfg)
        auth = GoogleAuth(cfg)
        try:
            tokens = auth.login(open_browser=not args.no_browser)
        except AuthError as exc:
            _err(f"Authorization failed: {exc}")
            return 1
        _print(f"Authorized. Access token valid for ~{int(tokens.expires_at - time.time())}s.")
        _print(f"Refresh token saved to {auth.token_path}")
        return 0
    if args.action == "logout":
        GoogleAuth(cfg).revoke()
        _print("Revoked Google access and removed stored tokens.")
        return 0
    _err(f"unknown auth action: {args.action}")
    return 2


def _print_job_progress(jobs: list, last: dict) -> None:
    for job in jobs:
        key = job.id
        if key in last and last[key] == (job.status, job.done, job.bytes_done):
            continue
        last[key] = (job.status, job.done, job.bytes_done)
        pct = int((job.done / job.total) * 100) if job.total else 0
        line = f"  [{job.status:>9}] {job.folder}  ({job.done}/{job.total}, {pct}%)"
        if job.bytes_done and job.bytes_total:
            line += f"  {format_bytes(job.bytes_done)}/{format_bytes(job.bytes_total)}"
        if job.current_file:
            line += f"\n             -> {Path(job.current_file).name}"
        if job.error:
            line += f"\n             !! {job.error}"
        _print(line)


def cmd_download(args) -> int:
    cfg = load_config()
    _apply_overrides(cfg, args)
    problems = cfg.validate()
    if problems and not args.no_cookies_ok:
        for p in problems:
            _err(f"config problem: {p}")

    downloader = PlaylistDownloader(cfg)
    db = StateDB()
    runner = JobRunner(cfg, db, downloader)
    runner.start()

    playlists: list[Playlist] = []
    if args.all:
        try:
            playlists = discover_playlists(cfg) if not args.url else discover_channel_playlists(cfg, args.url)
        except DiscoveryError:
            playlists = db.list_playlists()
        if getattr(args, "no_special", False):
            playlists = [p for p in playlists if not p.is_special]
    elif args.ids:
        saved = {p.id: p for p in db.list_playlists()}
        playlists = [saved[i] for i in args.ids if i in saved]
    elif args.url:
        url = normalize_playlist_url(args.url)
        playlists = [Playlist(title=args.title or url, url=url, kind="manual")]
    else:
        playlists = db.list_playlists()

    if args.limit:
        playlists = playlists[: args.limit]
    if not playlists:
        _err("No playlists to download. Use 'ytm2j discover' or pass a URL / --all.")
        runner.stop()
        return 1

    _print(f"Queueing {len(playlists)} playlist(s) -> {cfg.resolved_output_dir()}\n")
    jobs = runner.enqueue_many(playlists)

    last: dict = {}
    try:
        while True:
            current = {j.id: db.get_job(j.id) for j in jobs}
            current = {k: v for k, v in current.items() if v}
            _print_job_progress([j for j in current.values()], last)
            if all(j.status in {JobStatus.DONE.value, JobStatus.ERROR.value, JobStatus.CANCELLED.value} for j in current.values()):
                break
            time.sleep(2)
    except KeyboardInterrupt:
        _err("\nCancelling...")
        for j in jobs:
            runner.cancel(j.id)
        time.sleep(2)
    runner.stop()

    ok = sum(1 for j in jobs if db.get_job(j.id) and db.get_job(j.id).status == JobStatus.DONE.value)
    _print(f"\nDone: {ok}/{len(jobs)} playlist(s) downloaded successfully")
    return 0 if ok == len(jobs) else 1


def cmd_serve(args) -> int:
    from .server import create_app

    cfg = load_config()
    _apply_overrides(cfg, args)
    app = create_app(cfg)
    runner: JobRunner = app.state.runner
    runner.start()
    host = args.host
    port = args.port
    _print(f"ytm2j web UI:  http://{host}:{port}")
    if host == "0.0.0.0":
        _print("                (open http://localhost:%d on this machine)" % port)
    _print("Press Ctrl+C to stop.")
    try:
        import uvicorn

        uvicorn.run(app, host=host, port=port, log_level="info")
    except KeyboardInterrupt:
        pass
    finally:
        runner.stop()
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ytm2j", description="YouTube Music -> local folders, powered by yt-dlp.")
    p.add_argument("--version", action="version", version=f"ytm2j {__version__}")
    sub = p.add_subparsers(dest="command")

    sp = sub.add_parser("version", help="print version")
    sp.set_defaults(func=cmd_version)

    sp = sub.add_parser("doctor", help="diagnose the environment")
    sp.set_defaults(func=cmd_doctor)

    sp = sub.add_parser("discover", help="list playlists in your YouTube account")
    sp.add_argument("--url", help="a channel/@handle URL to scan instead of your account library")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_discover)

    sp = sub.add_parser("config", help="view or change settings")
    sp.add_argument("action", choices=["show", "set", "path"])
    sp.add_argument("key", nargs="?", default="")
    sp.add_argument("value", nargs="?", default="")
    sp.set_defaults(func=cmd_config)

    sp = sub.add_parser("auth", help="Google OAuth with YouTube (account connection)")
    sp.add_argument("action", choices=["login", "status", "logout", "setup"])
    sp.add_argument("--credentials", help="path to a Google OAuth client_secret.json")
    sp.add_argument("--no-browser", action="store_true", help="print the URL instead of opening a browser")
    sp.set_defaults(func=cmd_auth)

    sp = sub.add_parser("download", help="download playlists")
    sp.add_argument("url", nargs="?", help="a playlist URL, bare list ID, or @handle")
    sp.add_argument("--all", action="store_true", help="discover and download every playlist")
    sp.add_argument("--ids", nargs="*", help="download saved playlists by ID")
    sp.add_argument("--title", help="playlist title override (single URL mode)")
    sp.add_argument("--limit", type=int, help="only process N playlists")
    sp.add_argument("--no-special", action="store_true", help="skip Liked Songs / Watch Later / etc.")
    sp.add_argument("--format", choices=sorted(AUDIO_FORMATS), help="audio format")
    sp.add_argument("--quality", help="audio bitrate, e.g. 320")
    sp.add_argument("--output", help="output directory")
    sp.add_argument("--browser", help="cookie source browser (chrome, firefox, edge, ...)")
    sp.add_argument("--cookies", help="path to a Netscape cookies.txt")
    sp.add_argument("--ffmpeg", help="path to ffmpeg")
    sp.add_argument("--no-art", action="store_true", help="do not embed cover art")
    sp.add_argument("--dry-run", action="store_true", help="resolve but do not download")
    sp.add_argument("--no-cookies-ok", action="store_true", help="continue even without a cookie source")
    sp.set_defaults(func=cmd_download)

    sp = sub.add_parser("serve", help="run the web UI")
    sp.add_argument("--host", default="0.0.0.0")
    sp.add_argument("--port", type=int, default=8765)
    sp.add_argument("--format", choices=sorted(AUDIO_FORMATS))
    sp.add_argument("--quality")
    sp.add_argument("--output")
    sp.add_argument("--browser")
    sp.add_argument("--cookies")
    sp.add_argument("--ffmpeg")
    sp.set_defaults(func=cmd_serve)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        _err(f"error: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
