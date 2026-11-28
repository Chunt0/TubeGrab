"""Paths, file types, download formats and the locks shared across modules."""

import os
import re
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent   # the checkout / /app
# Everything TubeGrab writes lives under one folder (a NAS share in Docker).
DOWNLOAD_DIR = Path(os.environ.get("TUBEGRAB_DOWNLOADS") or BASE_DIR / "downloads").resolve()
META_DIR = DOWNLOAD_DIR / ".meta"
INDEX_FILE = META_DIR / "index.json"       # "<video id>:<kind>" → library path
FOLDERS_FILE = META_DIR / "folders.json"   # collection path → {title, url}
THUMBS_DIR = META_DIR / "thumbs"           # ffmpeg poster-frame cache (fallback)
PLAYLISTS_FILE = META_DIR / "playlists.json"   # the user's own playlists (references, not copies)
FAVORITES_FILE = META_DIR / "favorites.json"   # hearted artists (names) and songs (references); albums: in folders.json
DISLIKES_FILE = META_DIR / "dislikes.json"     # disliked artists and songs, like favorites; albums: in folders.json
PLAYS_FILE = META_DIR / "plays.json"           # how often / when each item was played: {ref: {n, last}}
PROGRESS_FILE = META_DIR / "progress.json"     # resume positions + watched flags
PL_COVERS_DIR = META_DIR / "playlists"         # uploaded playlist pictures
COVERS_DIR = META_DIR / "covers"               # uploaded album / collection pictures
SMALL_DIR = THUMBS_DIR / "sm"                   # grid-sized copies of thumbnails and covers
for _d in (DOWNLOAD_DIR, META_DIR, THUMBS_DIR, PL_COVERS_DIR, COVERS_DIR, SMALL_DIR):
    _d.mkdir(parents=True, exist_ok=True)
PLACEHOLDER = BASE_DIR / "static" / "placeholder.svg"
PLACEHOLDER_AUDIO = BASE_DIR / "static" / "placeholder-audio.svg"
WEB_DIST = BASE_DIR / "web" / "dist"      # built React UI (bun run build)
COOKIES_FILE = Path(os.environ.get("TUBEGRAB_COOKIES") or BASE_DIR / "cookies.txt")  # optional; see README
VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".m4v", ".mov", ".avi"}
AUDIO_EXTS = {".mp3", ".m4a", ".opus", ".ogg", ".wav", ".flac"}
MEDIA_EXTS = VIDEO_EXTS | AUDIO_EXTS
THUMB_EXTS = (".jpg", ".webp", ".png", ".jpeg")
ID_RE = re.compile(r"\[([A-Za-z0-9_-]{11})\]$")   # legacy "Title [id].ext" names
INDEX_PREFIX_RE = re.compile(r"^(\d{1,3}) - ")
YEAR_RE = re.compile(r"\s\((\d{4})\)$")
SECTIONS = ("Music", "Videos", "Playlists")
SINGLES = "Singles"

_lock = threading.Lock()
_jobs: dict[str, dict] = {}
_meta_lock = threading.RLock()
_tidy_lock = threading.Lock()


def _js_runtimes() -> dict:
    """YouTube needs a JS runtime for its player challenges. Use bun (bundled
    in the Docker image); fall back to deno/node if that's what's installed."""
    for name in ("bun", "deno", "node"):
        path = shutil.which(name) or (
            str(Path.home() / ".bun/bin/bun") if name == "bun" and (Path.home() / ".bun/bin/bun").exists() else None
        )
        if path:
            return {name: {"path": path}}
    return {"deno": {}}


JS_RUNTIMES = _js_runtimes()


def _video_fmt(height: int, max_kbps: int) -> str:
    """Cap resolution and video bitrate; yt-dlp's codec preference
    (AV1 > VP9 > H.264) then picks the most space-efficient stream."""
    return (
        f"bv[height<={height}][tbr<=?{max_kbps}]+ba/"
        f"bv[height<={height}]+ba/"
        f"b[height<={height}]/b"
    )


# Video is always 720p (AV1/VP9 preferred, bitrate-capped): ≈ 75 MB/h for
# slides/animation, up to ~500 MB/h for busy footage.
FORMATS = {
    "video": _video_fmt(720, 2500),
    # YouTube's AAC stream, kept as-is (no re-encode); other sources fall back to best audio
    "audio": "bestaudio[ext=m4a]/bestaudio/best",
}
FORMAT_ALIASES = {k: "video" for k in ("best", "2160p", "1080p", "720p", "480p")}   # old clients / saved choices
DEFAULT_FORMAT = "audio"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


SUB_LANGS = ["en", "en-US", "en-GB", "en-orig"]
SUB_OPTS = {"writesubtitles": True, "writeautomaticsub": True, "subtitleslangs": SUB_LANGS, "subtitlesformat": "vtt"}
