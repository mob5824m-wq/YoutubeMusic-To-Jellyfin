"""Google OAuth 2.0 (Authorization Code + PKCE) for YouTube Data API access.

yt-dlp downloads still need browser cookies (YouTube dropped OAuth for
InnerTube), but Google OAuth is the correct, robust way to *connect to the
account*: it lists every playlist and track through the official YouTube
Data API v3 without scraping fragile library endpoints.

Flow (loopback redirect, RFC 8252):

    ytm2j auth login          # or the "Connect Google" button in the UI
      -> open https://accounts.google.com/... in your browser
      -> Google redirects to http://localhost:<port>/auth/callback
      -> we exchange the code for tokens and store a refresh token

The refresh token is saved to ``google_tokens.json`` under the ytm2j config
dir with mode 0600 so access is renewed automatically.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import threading
import time
import webbrowser
from dataclasses import asdict, dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

from .config import Config, platform_config_dir

SCOPE = "https://www.googleapis.com/auth/youtube.readonly"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
DEFAULT_REDIRECT_PORT = 8766
CALLBACK_PATH = "/auth/callback"


class AuthError(Exception):
    """Raised for OAuth configuration / exchange failures."""


# --------------------------------------------------------------------- PKCE
def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def new_code_verifier() -> str:
    return _b64url(secrets.token_bytes(32))


def code_challenge(verifier: str) -> str:
    return _b64url(hashlib.sha256(verifier.encode("ascii")).digest())


# ------------------------------------------------------------------- tokens
@dataclass
class Tokens:
    access_token: str
    refresh_token: str
    token_type: str = "Bearer"
    expires_at: float = 0.0
    scope: str = SCOPE

    def expired(self, skew: int = 30) -> bool:
        return time.time() >= self.expires_at - skew

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Tokens":
        return cls(
            access_token=data.get("access_token", ""),
            refresh_token=data.get("refresh_token", ""),
            token_type=data.get("token_type", "Bearer"),
            expires_at=float(data.get("expires_at", 0)),
            scope=data.get("scope", SCOPE),
        )


# ------------------------------------------------------------------- helpers
def load_credentials_file(path: str | Path) -> dict[str, str]:
    """Read a Google *OAuth client* JSON (Desktop app) into client_id/secret."""
    raw = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    # Google writes either {"installed": {...}} or {"web": {...}}.
    for key in ("installed", "web", "android", "ios"):
        section = raw.get(key)
        if isinstance(section, dict) and section.get("client_id"):
            return {
                "google_client_id": section["client_id"],
                "google_client_secret": section.get("client_secret", ""),
            }
    if raw.get("client_id"):
        return {
            "google_client_id": raw["client_id"],
            "google_client_secret": raw.get("client_secret", ""),
        }
    raise AuthError(f"Could not find a client_id in {path}")


# ------------------------------------------------------------ callback server
class _CallbackServer(HTTPServer):
    allow_reuse_address = True

    def __init__(self, addr, handler):
        super().__init__(addr, handler)
        self.callback_event = threading.Event()
        self.callback_result: dict[str, str] = {}


class _CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path not in (CALLBACK_PATH, "/callback"):
            self.send_response(404)
            self.end_headers()
            return
        params = parse_qs(parsed.query)
        self.server.callback_result = {k: v[0] for k, v in params.items() if v}
        self.server.callback_event.set()
        if params.get("error"):
            body = (
                "<!doctype html><meta charset=utf-8>"
                "<h2>Authorization failed</h2><p>%s</p><p>Return to ytm2j.</p>"
                % (params.get("error_description", [""])[0],)
            ).encode()
        else:
            body = (
                "<!doctype html><meta charset=utf-8>"
                "<h2>Authorized</h2><p>You can close this window and go back to ytm2j.</p>"
            ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: Any) -> None:  # silence default logging
        return


class CallbackServer:
    """A loopback HTTP server that receives Google's redirect."""

    def __init__(self, port: int = 0, path: str = CALLBACK_PATH):
        self.requested_port = port
        self.path = path
        self._httpd: _CallbackServer | None = None
        self._thread: threading.Thread | None = None
        self._event = threading.Event()
        self._result: dict[str, str] = {}

    @property
    def port(self) -> int:
        return self._httpd.server_address[1] if self._httpd else self.requested_port

    @property
    def redirect_uri(self) -> str:
        return f"http://localhost:{self.port}{self.path}"

    def start(self) -> str:
        httpd = _CallbackServer(("127.0.0.1", self.requested_port), _CallbackHandler)
        httpd.callback_event = self._event
        httpd.callback_result = self._result
        self._httpd = httpd
        self._thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        self._thread.start()
        return self.redirect_uri

    def wait(self, timeout: float = 300.0) -> dict[str, str]:
        self._event.wait(timeout)
        return dict(self._result)

    def stop(self) -> None:
        if self._httpd:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None


# ------------------------------------------------------------------ GoogleAuth
class GoogleAuth:
    def __init__(self, cfg: Config):
        self.cfg = cfg

    @property
    def client_id(self) -> str:
        return self.cfg.google_client_id or os.environ.get("GOOGLE_CLIENT_ID", "")

    @property
    def client_secret(self) -> str:
        return self.cfg.google_client_secret or os.environ.get("GOOGLE_CLIENT_SECRET", "")

    @property
    def redirect_port(self) -> int:
        try:
            return int(self.cfg.google_redirect_port or DEFAULT_REDIRECT_PORT)
        except (TypeError, ValueError):
            return DEFAULT_REDIRECT_PORT

    def configured(self) -> bool:
        return bool(self.client_id)

    @property
    def token_path(self) -> Path:
        return platform_config_dir() / "google_tokens.json"

    # -- persistence ---------------------------------------------------- #
    def load_tokens(self) -> Tokens | None:
        p = self.token_path
        if not p.exists():
            return None
        try:
            return Tokens.from_dict(json.loads(p.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError, KeyError):
            return None

    def save_tokens(self, tokens: Tokens) -> None:
        p = self.token_path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(tokens.to_dict()), encoding="utf-8")
        try:  # only the owner may read the refresh token
            os.chmod(p, 0o600)
        except OSError:
            pass

    def clear_tokens(self) -> None:
        try:
            self.token_path.unlink(missing_ok=True)
        except OSError:
            pass

    # -- http ---------------------------------------------------------- #
    @staticmethod
    def _post_form(url: str, data: dict[str, str]) -> dict[str, Any]:
        body = urlencode(data).encode()
        req = Request(
            url,
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with urlopen(req, timeout=30) as resp:
                return json.load(resp)
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", "ignore")[:300]
            raise AuthError(f"OAuth request failed ({exc.code}): {detail}") from exc
        except (URLError, OSError) as exc:
            raise AuthError(f"Could not reach Google OAuth: {exc}") from exc

    def _to_tokens(self, payload: dict[str, Any]) -> Tokens:
        if "error" in payload:
            raise AuthError(payload.get("error_description") or payload["error"])
        if not payload.get("access_token"):
            raise AuthError("Google did not return an access token.")
        return Tokens(
            access_token=payload["access_token"],
            refresh_token=payload.get("refresh_token", ""),
            token_type=payload.get("token_type", "Bearer"),
            expires_at=time.time() + float(payload.get("expires_in", 3600)),
            scope=payload.get("scope", SCOPE),
        )

    # -- flow ---------------------------------------------------------- #
    def build_auth_url(self, redirect_uri: str, state: str, verifier: str) -> str:
        params = {
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": SCOPE,
            "access_type": "offline",
            "include_granted_scopes": "true",
            "prompt": "consent",
            "state": state,
            "code_challenge": code_challenge(verifier),
            "code_challenge_method": "S256",
        }
        return f"{AUTH_URL}?{urlencode(params)}"

    def exchange_code(self, code: str, verifier: str, redirect_uri: str) -> Tokens:
        data = {
            "code": code,
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
            "code_verifier": verifier,
        }
        if self.client_secret:
            data["client_secret"] = self.client_secret
        tokens = self._to_tokens(self._post_form(TOKEN_URL, data))
        self.save_tokens(tokens)
        return tokens

    def refresh(self) -> Tokens | None:
        current = self.load_tokens()
        if not current or not current.refresh_token:
            return None
        data = {
            "client_id": self.client_id,
            "refresh_token": current.refresh_token,
            "grant_type": "refresh_token",
        }
        if self.client_secret:
            data["client_secret"] = self.client_secret
        payload = self._post_form(TOKEN_URL, data)
        refresh = payload.get("refresh_token") or current.refresh_token
        tokens = Tokens(
            access_token=payload["access_token"],
            refresh_token=refresh,
            token_type=payload.get("token_type", "Bearer"),
            expires_at=time.time() + float(payload.get("expires_in", 3600)),
            scope=payload.get("scope", current.scope),
        )
        self.save_tokens(tokens)
        return tokens

    def get_valid_access_token(self) -> str | None:
        tokens = self.load_tokens()
        if not tokens or not tokens.access_token:
            return None
        if tokens.expired():
            try:
                refreshed = self.refresh()
            except AuthError:
                return None
            tokens = refreshed or tokens
        return tokens.access_token

    def revoke(self) -> None:
        tokens = self.load_tokens()
        if tokens and tokens.access_token:
            try:
                self._post_form(REVOKE_URL, {"token": tokens.access_token})
            except AuthError:
                pass
        self.clear_tokens()

    # -- one-shot flow ------------------------------------------------- #
    def create_flow(self, open_browser: bool = True) -> dict[str, Any]:
        if not self.configured():
            raise AuthError(
                "Google OAuth is not configured. Set google_client_id (and "
                "optionally google_client_secret), or run:\n"
                "  ytm2j auth setup --credentials <client_secret.json>"
            )
        server = CallbackServer(self.redirect_port)
        redirect_uri = server.start()
        state = secrets.token_urlsafe(24)
        verifier = new_code_verifier()
        url = self.build_auth_url(redirect_uri, state, verifier)
        if open_browser:
            try:
                webbrowser.open(url)
            except Exception:
                pass
        return {
            "url": url,
            "redirect_uri": redirect_uri,
            "state": state,
            "verifier": verifier,
            "server": server,
        }

    def complete_flow(self, flow: dict[str, Any], timeout: float = 300.0) -> Tokens:
        server: CallbackServer = flow["server"]
        try:
            result = server.wait(timeout)
        finally:
            server.stop()
        if result.get("error"):
            raise AuthError(result.get("error_description") or result["error"])
        if result.get("state") != flow["state"]:
            raise AuthError("State mismatch (possible CSRF attack) - aborted.")
        code = result.get("code")
        if not code:
            raise AuthError("Google did not return an authorization code.")
        return self.exchange_code(code, flow["verifier"], flow["redirect_uri"])

    def login(self, open_browser: bool = True, timeout: float = 300.0) -> Tokens:
        flow = self.create_flow(open_browser)
        print(f"Authorize ytm2j in your browser:\n\n  {flow['url']}\n")
        print(f"(Listening for the redirect on {flow['redirect_uri']})")
        return self.complete_flow(flow, timeout)


def status(cfg: Config) -> dict[str, Any]:
    """Describe the OAuth state for the UI / doctor."""
    auth = GoogleAuth(cfg)
    if not auth.configured():
        return {
            "configured": False,
            "connected": False,
            "reason": "Google client_id is not set (see Settings or GOOGLE_CLIENT_ID).",
        }
    tokens = auth.load_tokens()
    if not tokens or not tokens.access_token:
        return {
            "configured": True,
            "connected": False,
            "reason": "Not authorized yet. Run 'ytm2j auth login'.",
        }
    return {
        "configured": True,
        "connected": True,
        "expires_at": tokens.expires_at,
        "expires_in": int(max(0, tokens.expires_at - time.time())),
        "refresh_token": bool(tokens.refresh_token),
        "scope": tokens.scope,
    }
