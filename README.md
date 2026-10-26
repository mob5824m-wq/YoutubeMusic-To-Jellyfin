# ytm2j — YouTube Music → local folders, one folder per playlist

`ytm2j` connects to **your YouTube Music account** and downloads **every
playlist** into **one folder per playlist**, using [`yt-dlp`](https://github.com/yt-dlp/yt-dlp).
It ships as a **local web app** (open it in any browser on any machine) **and**
a CLI, so it works the same way on **Windows, macOS, Linux**, in a container,
or on a headless server.

```
Your YouTube Music account
        │
        ▼
┌─────────────────────────────────────────────────────────┐
│  ytm2j  (web UI + CLI, powered by yt-dlp + ffmpeg)      │
└─────────────────────────────────────────────────────────┘
        │
        ▼
~/Music/YouTube Music/
├── Liked Songs/          ← folder per playlist
│   ├── Track One.mp3     ← cover art + tags embedded
│   └── Track Two.mp3
├── Road Trip 2024/
├── Workout Mix/
└── ...
```

## Features

- 🔍 **Account discovery** — lists your playlists, plus Liked Songs, Watch
  Later and the rest of your library, via the **YouTube Data API** (Google
  OAuth), with a cookie-scraping fallback.
- 📁 **One folder per playlist** — folder names are sanitised for every OS
  (illegal `/ \ : * ? " < > |` characters are cleaned up).
- 🎵 **Proper audio** — extracts audio with `ffmpeg` and embeds **cover art**
  and **ID3/metadata tags**, so the files are Jellyfin-ready.
- 🔐 **Private playlists** — works with your browser cookies
  (`--cookies-from-browser`) or a Netscape `cookies.txt`.
- 🖱️ **Web UI** — run it once, open `http://localhost:8765` on any device on
  your network, pick playlists and watch live progress.
- ⚙️ **CLI** — script it, run it in CI, or use it on a server with no GUI.
- 🧱 **Resumable & deduped** — a download archive skips tracks you already
  have; interrupted jobs are re-queued on restart.
- 📦 **All platforms** — launchers for Windows (`.bat`), macOS (`.command`)
  and Linux (`.sh`), plus a `Dockerfile`, Docker Compose and a PyInstaller
  spec for real `.exe` / `.app` / ELF binaries.

## Quick start

### 1. Install Python 3.9+ (if you don't have it)

- **Windows:** install from [python.org](https://python.org/downloads/) (tick
  *Add Python to PATH*).
- **macOS:** `brew install python` or use the system Python.
- **Linux:** `sudo apt install python3 python3-venv`.

### 2. Run it

Pick **one**:

```bash
# A) Clone and launch (auto-creates a venv and installs everything)
git clone https://github.com/mob5824m-wq/YoutubeMusic-To-Jellyfin.git
cd YoutubeMusic-To-Jellyfin
./scripts/run.sh          # Linux/macOS
scripts\run.bat           # Windows (double-clickable)
# macOS users can also double-click scripts/run.command

# B) Or install it as a proper package
pip install .
ytm2j serve

# C) Or run the CLI only
pip install .
ytm2j doctor
ytm2j download --all
```

Then open **http://localhost:8765**.

> **ffmpeg:** the `ffmpeg` extra (`imageio-ffmpeg`) bundles a cross-platform
> ffmpeg binary automatically, so most people need nothing else. If you
> already have ffmpeg, point `ffmpeg_location` at it in Settings.

### 3. Connect to your account (needed for private playlists)

**Recommended:** use Google OAuth — see
[Google OAuth setup](#google-oauth-setup-recommended) below. It connects to
your account through the official YouTube Data API.

For downloading private tracks you also need cookies. In **Settings** (the
gear button) choose a cookie source:

- **Browser** — pick e.g. `Chrome / Chromium`, `Firefox`, `Microsoft Edge`,
  `Brave`. Make sure you are signed in to YouTube in that browser.
- **cookies.txt** — export a Netscape-format file with an extension such as
  *Get cookies.txt LOCALLY* and point the setting at it.

> **Tip (important):** YouTube rotates cookies on open tabs. For a cookie
> file that keeps working, export it from a **private/incognito** window where
> the *only* tab is `https://www.youtube.com/robots.txt`, then close the
> window. See the [yt-dlp cookie wiki](https://github.com/yt-dlp/yt-dlp/wiki/extractors).

Click **Discover playlists** and your library appears. Select all (or a few),
hit **Download selected**, and watch the **Downloads** panel.

## Google OAuth setup (recommended)

Connect ytm2j to your YouTube account with Google OAuth and use the official
**YouTube Data API v3** to list your playlists and tracks. This is the most
reliable way to see your whole library — including Liked Songs — and it avoids
scraping fragile YouTube web endpoints.

> **What it's used for:** OAuth powers **account connection + discovery**
> (listing playlists and their tracks). Downloads still go through yt-dlp,
> which needs browser cookies for private content — see step 3 above.

### 1. Create a Google OAuth client

**a) Enable the API**

1. Open the [Google Cloud Console](https://console.cloud.google.com/apis/credentials)
   and select (or create) a project.
2. **APIs & Services → Library** → search for **YouTube Data API v3** → **Enable**.

**b) Set up the OAuth consent screen** (required before you can create a client)

1. **APIs & Services → OAuth consent screen** → choose **External**.
2. App name (e.g. `ytm2j`) and a contact email.
3. **Test users** → add **your own email**. While the app is in *Testing*
   status, only these accounts can approve access.
4. **Scopes** → add `https://www.googleapis.com/auth/youtube.readonly`.
5. **Save**.

**c) Create the client**

1. **APIs & Services → Credentials** → **Create credentials → OAuth client ID**.
2. **Application type:** **Desktop app**.
3. **Download the JSON** file (named `client_secret.json`).

> **Desktop app** clients accept any loopback redirect URI, so the app's
> `http://localhost:8766/auth/callback` works with no extra configuration —
> there's no "authorized redirect URIs" field to fill in.

### 2. Give ytm2j the credentials

Pass the downloaded file directly:

```bash
ytm2j auth setup --credentials client_secret.json
```

…or set the values in **Settings** (gear icon → *Google account*), or via the
CLI and environment:

```bash
ytm2j config set google_client_id    <id>.apps.googleusercontent.com
ytm2j config set google_client_secret <secret>

# or
export GOOGLE_CLIENT_ID=...
export GOOGLE_CLIENT_SECRET=...
```

### 3. Authorize

```bash
ytm2j auth login
```

ytm2j starts a loopback server on `http://localhost:8766/auth/callback`, opens
Google's consent screen in your browser, and exchanges the returned code for
tokens. A refresh token is stored in `google_tokens.json` (mode `0600`) and
refreshed automatically.

Check the state any time:

```bash
ytm2j auth status     # {configured, connected, expires_in, ...}
ytm2j doctor          # also reports google_oauth status
```

Revoke access and delete the stored tokens:

```bash
ytm2j auth logout
```

### 4. Or, from the web UI

Click **Connect Google** in the header (or open Settings → *Google account*).
The authorization URL is copied to your clipboard, and a **Paste the redirect
URL or code** box handles the case where the browser can't reach the loopback
port (containers, remote machines, WSL).

### Notes

- The token requests the `https://www.googleapis.com/auth/youtube.readonly`
  scope — read-only access to your YouTube account. No upload or delete rights.
- **Testing status:** while the client is unpublished, only the email(s) you
  listed under *Test users* can approve the consent screen. Publishing to
  production needs a Privacy Policy URL and Google verification — unnecessary
  for a personal setup.
- OAuth lists playlists and tracks via the **YouTube Data API**. Downloads
  still use yt-dlp, and for **private/liked** content yt-dlp needs cookies —
  so configure a cookie source as well.

## CLI reference

```bash
ytm2j doctor                      # check yt-dlp, ffmpeg, cookies, output dir
ytm2j discover                    # list your playlists
ytm2j discover --json
ytm2j download --all              # discover + download every playlist
ytm2j download "https://music.youtube.com/playlist?list=PL..."
ytm2j download "PLabcdefghijk" --format m4a --quality 256
ytm2j config show                 # view settings
ytm2j config set audio_format m4a
ytm2j auth setup --credentials client_secret.json   # load a Google OAuth client
ytm2j auth login                    # open Google's consent screen
ytm2j auth status                   # is Google connected?
ytm2j auth logout                   # revoke access + delete tokens
ytm2j serve --host 0.0.0.0 --port 8765
```

Global overrides work on every command: `--format`, `--quality`, `--output`,
`--browser`, `--cookies`, `--ffmpeg`, `--no-art`, `--dry-run`.

## Configuration

Settings live in a JSON file (created automatically):

| OS | Path |
|----|------|
| Windows | `%APPDATA%\ytm2j\config.json` |
| macOS | `~/Library/Application Support/ytm2j/config.json` |
| Linux | `~/.config/ytm2j/config.json` (`XDG_CONFIG_HOME` honoured) |

Common keys: `output_dir`, `audio_format` (`mp3`, `m4a`, `opus`, `vorbis`,
`flac`, `wav`), `audio_quality`, `cookie_browser`, `cookie_file`,
`google_client_id`, `google_client_secret`, `google_redirect_port`,
`embed_thumbnail`, `add_metadata`, `ffmpeg_location`, `player_client`,
`download_archive`, `concurrent`, `playlist_folder_template`.

Environment overrides (great for containers): `YTM2J_OUTPUT_DIR`,
`YTM2J_AUDIO_FORMAT`, `YTM2J_COOKIE_BROWSER`, `YTM2J_COOKIE_FILE`,
`GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`.

## Jellyfin

Point Jellyfin at your output directory and it will pick up the per-playlist
folders automatically (it reads the embedded tags for artist/album). With
`--embed-thumbnail` the cover art is embedded in the audio files, so album art
shows up in the Jellyfin web UI too. For multiple libraries you can map each
playlist folder to its own Jellyfin *Music* library.

## Docker

```bash
docker compose -f docker/docker-compose.yml up --build
# → http://localhost:8765
```

Drop a Netscape-format `cookies.txt` next to the compose file and uncomment
the bind mount in `docker-compose.yml` to unlock private playlists.

## Build a standalone app

```bash
pip install pyinstaller
python scripts/build.py
# → ./dist/ytm2j(.exe|.app)   (native build per OS; PyInstaller can't cross-compile)
```

## Project layout

```
ytm2j/
  cli.py         command line interface
  server.py      FastAPI web server + JSON API
  discover.py    account playlist discovery
  downloader.py  yt-dlp option building + downloads
  runner.py      background job queue
  db.py          SQLite job/playlist registry
  cookies.py     cookie-source detection
  config.py      settings
  static/        the single-page web UI
scripts/         launchers, PyInstaller spec, build helper
docker/          Dockerfile + compose
tests/           pytest suite (yt-dlp mocked)
```

## Troubleshooting

- **Google not connected / `google_client_id is not set`** — run
  `ytm2j auth setup --credentials client_secret.json` then `ytm2j auth login`
  (see *Google OAuth setup* above). `ytm2j auth status` reports the state.
- **`no cookies configured`** — expected for public playlists, but Liked Songs
  and private playlists need auth. Google OAuth lists them, but downloading
  private content still needs a cookie source.
- **`Unable to download ... TLS/SSL` / `HTTP Error 403`** — YouTube
  throttling; try a `player_client` such as `web_music` or `tv` in Settings, or
  throttle with `--concurrent 1`.
- **Some tracks fail** — YouTube regularly breaks clients. Update yt-dlp
  (`pip install -U yt-dlp`) — `ytm2j doctor` shows your version.
- **PO token required** — some YouTube clients now require a *Proof of Origin*
  token. Use the [bgutil PO token provider](https://github.com/yt-dlp/yt-dlp-get-pot)
  with a matching `player_client`.
- **ffmpeg missing** — `pip install imageio-ffmpeg` (the default `ffmpeg`
  extra) or install ffmpeg system-wide.

## Legal

This tool downloads content for **personal, offline use only**. Respect YouTube's
Terms of Service and the copyright status of the material. You are responsible
for how you use downloaded content.

## License

MIT — see [LICENSE](LICENSE).
