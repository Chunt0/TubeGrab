#!/usr/bin/env python3
"""
TubeGrab — a tiny local web UI around yt-dlp.

Paste a YouTube video or playlist URL (your own uploads or content you are
licensed to download), pick a quality, and the file is filed into the library
automatically:

    Music/<Artist>/<Album (Year)>/<NN - Track>.m4a     audio (album or single)
    Music/<Artist>/Singles/<Track>.m4a                 audio without album info
    Videos/<Channel>/<Title>.mp4                       single videos
    Playlists/<Playlist>/<NNN - Title>.mp4             any other video playlist
    .meta/                                             index, metadata, thumbnails

Run:  ./run.sh   →  http://127.0.0.1:8765
"""

import hashlib
import json
import mimetypes
import os
import re
import shutil
import sqlite3
import subprocess
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, Response, jsonify, request, send_file, send_from_directory
from werkzeug.wsgi import wrap_file
import yt_dlp
from yt_dlp.postprocessor.common import PostProcessor
from yt_dlp.postprocessor.sponsorblock import SponsorBlockPP

BASE_DIR = Path(__file__).resolve().parent
# Everything TubeGrab writes lives under one folder (a NAS share in Docker).
DOWNLOAD_DIR = Path(os.environ.get("TUBEGRAB_DOWNLOADS") or BASE_DIR / "downloads").resolve()
META_DIR = DOWNLOAD_DIR / ".meta"
INDEX_FILE = META_DIR / "index.json"       # "<video id>:<kind>" → library path
FOLDERS_FILE = META_DIR / "folders.json"   # collection path → {title, url}
THUMBS_DIR = META_DIR / "thumbs"           # ffmpeg poster-frame cache (fallback)
PLAYLISTS_FILE = META_DIR / "playlists.json"   # the user's own playlists (references, not copies)
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

app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024   # playlist picture uploads

_GZIP_TYPES = ("application/json", "text/html", "text/css", "text/javascript", "application/javascript", "image/svg+xml")


@app.after_request
def _changed(resp):
    """Edits to the library are in the index before their answer goes out, so
    whatever the client fetches next already shows them."""
    if (request.method in ("POST", "PATCH", "PUT", "DELETE")
            and not request.path.startswith(("/api/progress", "/api/download", "/api/jobs", "/api/playlists"))):
        _indexer.flush()
    return resp


@app.after_request
def _gzip(resp):
    """Compress text responses: the UI is often used over slow phone links."""
    if (resp.status_code != 200 or resp.mimetype not in _GZIP_TYPES or "Content-Encoding" in resp.headers
            or "gzip" not in (request.headers.get("Accept-Encoding") or "")):
        return resp
    resp.direct_passthrough = False   # files from send_file
    data = resp.get_data()
    if len(data) < 1024:
        return resp
    import gzip
    resp.set_data(gzip.compress(data, 6))
    resp.headers["Content-Encoding"] = "gzip"
    resp.headers.add("Vary", "Accept-Encoding")
    return resp

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


# ---------------------------------------------------------------- naming

def _clean(name: str | None, fallback: str = "Unknown", limit: int = 120) -> str:
    """A file/folder name that's safe on Linux, Windows and SMB shares."""
    s = (name or "").replace(":", " -").replace("/", "-").replace("\\", "-")
    s = re.sub(r"\s", " ", s)               # (tabs, line breaks: a space, not nothing)
    s = re.sub(r'[<>"|?*\x00-\x1f]', "", s)
    s = re.sub(r"\s+", " ", s).strip().rstrip(". ")
    s = s.lstrip(".")                       # no hidden files
    return (s[:limit].rstrip(". ") or fallback)


def _clean_channel(name: str | None) -> str | None:
    """'Coldsteel - Topic' → 'Coldsteel' (YouTube's auto-generated artist channels)."""
    return re.sub(r" - Topic$", "", name) if name else name


def _year(info: dict) -> str | None:
    y = info.get("release_year") or (info.get("release_date") or "")[:4]
    return str(y) if y and str(y).isdigit() else None


def _artist(info: dict) -> str | None:
    """Album artist for folder names: the first credited artist."""
    for key in ("album_artist", "album_artists", "artists", "artist", "creators"):
        v = info.get(key)
        if isinstance(v, list):
            v = v[0] if v else None
        if v:
            return str(v).split(", ")[0] if key == "artist" else str(v)
    return None


# "(Official Music Video)", "[Official Lyric Video]", "(Visualizer)", "(Official Audio HD)"…
_NOISE_WORDS = re.compile(r"^(?:official|music|lyrics?|audio|video|visuali[sz]er|mv|hd|4k|\s|-)+$", re.I)


def _track_title(title: str, artist: str | None) -> str:
    """'Taylor Swift - Fortnight (feat. Post Malone) (Official Music Video)' → 'Fortnight (feat. Post Malone)'.
    Keeps meaningful brackets like '(feat. …)', '(Live at …)', '(Remix)'."""
    t = title
    if artist:
        t = re.sub(rf"^\s*{re.escape(artist)}\s*[-–—:|]\s*", "", t, flags=re.I)
    t = re.sub(r"\s*[\(\[]([^\)\]]*)[\)\]]",
               lambda m: "" if _NOISE_WORDS.match(m.group(1)) else m.group(0), t)
    t = re.sub(r"\s+", " ", t).strip(" -–—")
    return t or title


def _fold(name: str) -> str:
    """Comparison key for artist/album names: 'MELVINS' == 'Melvins'."""
    return _clean(name).casefold()


def _artist_dir(artist: str) -> str:
    """Existing Music/<artist> folder spelled differently ('MELVINS' vs 'Melvins'), else a new name."""
    music = DOWNLOAD_DIR / "Music"
    if music.is_dir():
        for d in music.iterdir():
            if d.is_dir() and _fold(d.name) == _fold(artist):
                return d.name
    return _clean(artist)


def _album_folder(artist: str, album: str, year: str | None, url: str | None) -> tuple[str, str, str | None]:
    """Where an album lives, reusing an existing folder so tracks whose metadata
    disagrees (YouTube gives some tracks a year or a differently-cased artist,
    others not) don't split the album. Returns (artist folder, album folder, year)."""
    adir = _artist_dir(artist)
    base = _fold(album)
    candidates: list[tuple[str, str]] = []
    for key, v in _folders().items():
        parts = key.split("/")
        if len(parts) == 3 and parts[0] == "Music" and (DOWNLOAD_DIR / key).is_dir() and (
                (url and v.get("url") == url) or
                (_fold(parts[1]) == _fold(adir) and _fold(YEAR_RE.sub("", parts[2])) == base)):
            candidates.append((parts[1], parts[2]))
    d = DOWNLOAD_DIR / "Music" / adir
    if d.is_dir():
        candidates += [(adir, p.name) for p in d.iterdir()
                       if p.is_dir() and _fold(YEAR_RE.sub("", p.name)) == base]
    if not candidates:
        return adir, (f"{_clean(album)} ({year})" if year else _clean(album)), year
    pick = next((c for c in candidates if YEAR_RE.search(c[1])), candidates[0])   # prefer one with a year
    m = YEAR_RE.search(pick[1])
    return pick[0], pick[1], (m.group(1) if m else year)


def _is_album_playlist(info: dict) -> bool:
    # YouTube Music's auto-generated album playlists
    return str(info.get("playlist_id") or "").startswith("OLAK5uy_")


def _plan(info: dict, *, audio: bool, playlist_mode: bool, album: dict | None = None) -> dict:
    """Decide where an entry goes. Returns {rel (no extension), section, tags}.
    `album`: this playlist was identified as one album (_identify_album)."""
    title = info.get("track") or info.get("title") or info.get("id") or "Untitled"
    in_playlist = playlist_mode and bool(info.get("playlist_id"))
    album_pl = in_playlist and _is_album_playlist(info)
    artist = _artist(info)
    tags: dict = {}

    has_album = bool(info.get("album") and artist)
    # a regular playlist that is really one album ("The Smashing Pumpkins: Gish (Deluxe Edition)")
    album_like = album_pl or (in_playlist and has_album and len(_fold(info["album"])) >= 3
                              and _fold(info["album"]) in _fold(info.get("playlist_title") or ""))
    if audio and in_playlist and album and not has_album and not album_pl:
        # fan uploads of one album: Music/Artist/Album (Year)/NN - Track, as the album has it
        t = album["tracks"].get(info.get("id")) or {
            "no": None, "title": _local_title(title, album.get("guess_artist"), album.get("guess_album"))}
        adir, folder, year = _album_folder(album["artist"], album["album"], album.get("year"), album.get("url"))
        no = t.get("no")
        name = f"{int(no):02d} - {_clean(t['title'])}" if no else _clean(t["title"])
        tags = {"artist": adir, "album_artist": adir, "album": album["album"], "track_number": no,
                "release_year": year, "track": t["title"]}
        return {"rel": f"Music/{adir}/{folder}/{name}", "section": "Music", "tags": tags, "mixed_playlist": False,
                "collection_title": album["album"], "collection_url": album.get("url")}
    # audio goes to Music when YouTube knows the song; a mixed playlist's songs
    # land in their own albums (and become one of your playlists, see _run_job)
    if audio and (album_like or artist) and (not in_playlist or album_like or has_album):
        # Music: Artist / Album (Year) / NN - Track
        artist = artist or _clean_channel(info.get("uploader") or info.get("channel"))
        album = info.get("album") or (info.get("playlist_title") if album_pl else None)
        year = _year(info)
        if not info.get("track"):   # a music video, not a YouTube Music track
            title = _track_title(title, artist)
        track_no = info.get("playlist_index") if album_like else info.get("track_number")
        if album:
            url = info.get("playlist_webpage_url") if album_like else None
            adir, folder, year = _album_folder(artist, album, year, url)
        else:
            adir, folder = _artist_dir(artist), SINGLES
        name = f"{int(track_no):02d} - {_clean(title)}" if track_no and album else _clean(title)
        rel = f"Music/{adir}/{folder}/{name}"
        artist = adir   # album artist = the folder's spelling, so tags agree across the album
        tags = {"artist": info.get("artist") or artist, "album_artist": artist, "album": album,
                "track_number": track_no if album else None, "release_year": year, "track": title}
        return {"rel": rel, "section": "Music", "tags": tags, "mixed_playlist": in_playlist and not album_like,
                "collection_title": album, "collection_url": info.get("playlist_webpage_url") if album_like else None}

    if audio:
        # audio is music: a song YouTube knows nothing about goes to its artist's singles,
        # read from an 'Artist - Song' title, else the channel (from a playlist, the
        # playlist itself becomes one of your playlists, see _run_job)
        channel = _clean_channel(info.get("uploader") or info.get("channel")) or "Unknown"
        parts = re.split(r"\s+[-–—]\s+", title, maxsplit=1)
        if len(parts) == 2 and 0 < len(parts[0]) <= 40 and parts[1].strip():
            channel, title = parts[0].strip(), _track_title(parts[1], None)
        adir = _artist_dir(channel)
        return {"rel": f"Music/{adir}/{SINGLES}/{_clean(title)}", "section": "Music",
                "tags": {"artist": channel, "album_artist": adir, "track": title}, "mixed_playlist": in_playlist,
                "collection_title": None, "collection_url": None}

    if in_playlist:
        pl = info.get("playlist_title") or "Playlist"
        idx = info.get("playlist_index")
        name = f"{int(idx):03d} - {_clean(title)}" if idx else _clean(title)
        return {"rel": f"Playlists/{_clean(pl)}/{name}", "section": "Playlists", "tags": tags,
                "collection_title": pl, "collection_url": info.get("playlist_webpage_url")}

    channel = _clean_channel(info.get("uploader") or info.get("channel"))
    return {"rel": f"Videos/{_clean(channel)}/{_clean(title)}", "section": "Videos", "tags": tags,
            "collection_title": channel, "collection_url": info.get("channel_url") or info.get("uploader_url")}


# ---------------------------------------------------------------- album playlists
# Someone's "Primus - Frizzle Fry (full album)" playlist of fan uploads carries no
# music metadata, so _plan would file it as a plain playlist. Before an audio
# playlist downloads we look at its title and entry titles; when it looks like
# one album, MusicBrainz (free, open music database; only the guessed artist and
# album name are sent) confirms it and gives the real track list, numbers, year
# and cover. Answers are kept per playlist in .meta/albums.json.

ALBUMS_FILE = META_DIR / "albums.json"
MUSICBRAINZ = "https://musicbrainz.org/ws/2"
COVER_ART = "https://coverartarchive.org"
# "Primus- To Defy", "PRIMUS: Spaghetti Western", "Malloy - Primus" (not "Skinny-Constantinople")
_SEP = re.compile(r"\s+[-–—|:~]+\s*|\s*[-–—|:~]+\s+")
_ALBUM_WORDS = re.compile(
    r"[\(\[]?\s*\b(?:(?:the\s+)?(?:full|complete|whole|entire)\s+(?:album|lp|ep|record)|album\s+(?:completo|complet|completa)"
    r"|full\s*-\s*album|fullalbum)\b\s*[\)\]]?", re.I)
_PL_JUNK = re.compile(r"[\(\[]\s*(?:hq|hd|high quality|remaster(?:ed)?|\d{4}|lyrics?|audio)\s*[\)\]]", re.I)
_FILE_EXT = re.compile(r"\.(?:wmv|avi|mp4|flv|mov|mkv|mp3|m4a|wav)\b", re.I)
_TRACK_NUM = re.compile(r"^\s*(?:track\s*)?\d{1,2}\s*[.)\-–]\s+|\s*#\d{1,2}\s*$", re.I)
_mb_last = 0.0
_albums_lock = threading.Lock()


def _norm(s: str) -> str:
    """Loose comparison form: 'Hello Skinny / Constantinople' == 'hello skinny-constantinople'."""
    import unicodedata
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c)).casefold().replace("&", " and ")
    s = re.sub(r"[\(\[][^\)\]]*[\)\]]", " ", s)
    return re.sub(r"[^\w]+", " ", s).strip()


def _http_json(url: str, *, mb: bool = False):
    import urllib.error
    import urllib.request
    global _mb_last
    req = urllib.request.Request(url, headers={"User-Agent": "TubeGrab/1.0 (self-hosted music library)",
                                               "Accept": "application/json"})
    for attempt in range(3):
        if mb:
            wait = _mb_last + 1.1 - time.monotonic()   # MusicBrainz asks for ≤ 1 request / s
            if wait > 0:
                time.sleep(wait)
            _mb_last = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code in (429, 503) and attempt < 2:
                time.sleep(3)
                continue
            raise


def _guess_album(title: str, entries: list[dict]) -> dict | None:
    """From the playlist and entry titles: {artist, album, year, said} or None.
    `said` = the playlist calls itself an album ('(full album)')."""
    n = len(entries)
    if n < 3:
        return None
    counts: dict[str, int] = {}
    spellings: dict[str, list[str]] = {}
    for e in entries:
        parts = [p.strip() for p in _SEP.split(e.get("title") or "") if p.strip()]
        if len(parts) < 2:
            continue
        for p in {parts[0], parts[-1]}:
            k = _norm(p)
            if k:
                counts[k] = counts.get(k, 0) + 1
                spellings.setdefault(k, []).append(p)
    said = bool(_ALBUM_WORDS.search(title))
    rest = _ALBUM_WORDS.sub(" ", title)
    # "(1990)", "[1990]" or a trailing 1990 (not "Taylor Swift - 1989 (Taylor's Version)")
    m = re.search(r"[\(\[]\s*(19[5-9]\d|20[0-4]\d)\s*[\)\]]|[\s,-](19[5-9]\d|20[0-4]\d)\s*$", rest)
    year = (m.group(1) or m.group(2)) if m else None
    if m:
        rest = rest[:m.start()] + " " + rest[m.end():]
    rest = re.sub(r"[\(\[]\s*[\)\]]", " ", _PL_JUNK.sub(" ", rest))
    artist = None
    best = max(counts.items(), key=lambda kv: kv[1], default=None)
    if best and best[1] >= max(2, 0.6 * n):
        # the album name can repeat in every title too ('… - Frizzle Fry'): the artist is the one the playlist starts with
        top = [k for k, c in counts.items() if c >= max(2, 0.6 * n)]
        starts = [k for k in top if _norm(rest).startswith(k)]
        key = starts[0] if starts else best[0]
        names = spellings[key]
        artist = sorted(set(names), key=lambda s: (s.isupper(), -names.count(s)))[0]
    album = rest
    if artist:
        a = re.escape(_norm(artist)).replace(r"\ ", r"[^\w]+")
        album = re.sub(rf"^\W*{a}\W*|\W*{a}\W*$", " ", rest, flags=re.I) if re.search(a, _norm(rest)) else rest
        # "Primus Frizzle Fry": strip the artist's words however they're written
        words = artist.split()
        album = re.sub(r"^\W*" + r"\W+".join(map(re.escape, words)) + r"(?:'s)?\W*", "", album.strip(), flags=re.I)
        album = re.sub(r"\W*" + r"\W+".join(map(re.escape, words)) + r"\W*$", "", album, flags=re.I)
    album = re.sub(r"\s+", " ", album).strip(" -–—|:~.,")
    if not album or (not artist and not said):
        return None
    return {"artist": artist, "album": album, "year": year, "said": said}


def _guesses(g: dict) -> list[dict]:
    """Ways to read the guess: as is, or, with no artist in the entry titles,
    'Artist - Album' / 'Nirvana Nevermind' split from the playlist title."""
    if g["artist"]:
        return [g]
    parts = [p.strip() for p in _SEP.split(g["album"]) if p.strip()]
    if len(parts) == 2:
        return [{**g, "artist": parts[0], "album": parts[1]}, {**g, "artist": parts[1], "album": parts[0]}]
    words = g["album"].split()
    return [{**g, "artist": " ".join(words[:k]), "album": " ".join(words[k:])} for k in range(1, min(4, len(words)))]


def _local_title(title: str, artist: str | None, album: str | None) -> str:
    """'Primus- Mr. Knowitall- Frizzle Fry' → 'Mr. Knowitall'."""
    drop = {_norm(x) for x in (artist, album) if x}
    parts = [p.strip() for p in _SEP.split(title) if p.strip()]
    keep = [p for p in parts if _norm(p) not in drop] or parts
    t = " - ".join(keep)
    t = _TRACK_NUM.sub("", _FILE_EXT.sub("", t))
    return _track_title(t, None)


def _mb_quote(s: str) -> str:
    return '"' + s.replace("\\", " ").replace('"', " ") + '"'


def _mb_release(guess: dict, entries: list[dict]) -> dict | None:
    """The MusicBrainz release whose track list best matches the entries."""
    import urllib.parse
    q = f"release:{_mb_quote(guess['album'])}"
    if guess["artist"]:
        q += f" AND artist:{_mb_quote(guess['artist'])}"
    found = _http_json(f"{MUSICBRAINZ}/release/?{urllib.parse.urlencode({'query': q, 'limit': 25, 'fmt': 'json'})}", mb=True)
    cands = [r for r in (found or {}).get("releases", []) if r.get("score", 0) >= 90]
    if not cands:
        return None
    n = len(entries)
    cands.sort(key=lambda r: (abs((r.get("track-count") or 0) - n), r.get("status") != "Official",
                              (r.get("release-group") or {}).get("primary-type") != "Album"))
    best, seen = None, set()
    for r in cands:
        tc = r.get("track-count")
        if tc in seen:
            continue
        seen.add(tc)
        full = _http_json(f"{MUSICBRAINZ}/release/{r['id']}?inc=recordings+artist-credits+release-groups&fmt=json", mb=True)
        if not full:
            continue
        tracks = [t for m in full.get("media") or [] for t in m.get("tracks") or []]
        pairs = _match_tracks(entries, [t.get("title") or "" for t in tracks], guess)
        if not best or len(pairs) > len(best[1]):
            best = (full, pairs, tracks)
        if len(seen) >= 3 or len(pairs) == n:
            break
    if not best:
        return None
    full, pairs, tracks = best
    if len(pairs) < max(3, 0.6 * min(n, len(tracks))):
        return None
    rg = full.get("release-group") or {}
    credit = full.get("artist-credit") or []
    artist = "".join((c.get("name") or "") + (c.get("joinphrase") or "") for c in credit).strip() or guess["artist"]
    year = (rg.get("first-release-date") or full.get("date") or "")[:4]
    return {"artist": artist, "album": full.get("title") or guess["album"],
            "year": year if year.isdigit() else guess.get("year"),
            "mbid": full["id"], "rgid": rg.get("id"),
            "tracks": {entries[i]["id"]: {"no": j + 1, "title": tracks[j]["title"]} for i, j in pairs.items()}}


def _match_tracks(entries: list[dict], titles: list[str], guess: dict) -> dict[int, int]:
    """entry index → track index, best pairs first, each track used once."""
    import difflib
    tn = [_norm(t) for t in titles]
    scored = []
    for i, e in enumerate(entries):
        en = _norm(_local_title(e.get("title") or "", guess["artist"], guess["album"]))
        full = _norm(e.get("title") or "")
        for j, t in enumerate(tn):
            if not t or not en:
                continue
            s = difflib.SequenceMatcher(None, en, t).ratio()
            if len(t) >= 4 and (re.search(rf"\b{re.escape(t)}\b", en) or re.search(rf"\b{re.escape(t)}\b", full)):
                s = max(s, 0.86)
            if s >= 0.8:
                scored.append((s, i, j))
    pairs: dict[int, int] = {}
    used: set[int] = set()
    for s, i, j in sorted(scored, reverse=True):
        if i not in pairs and j not in used:
            pairs[i] = j
            used.add(j)
    return pairs


def _identify_album(pl_id: str, title: str, entries: list[dict], url: str | None = None) -> dict | None:
    """{artist, album, year, url, mbid?, tracks: {video id: {no, title}}} when this
    playlist is one album, else None. `entries`: [{id, title}] in playlist order."""
    entries = [e for e in entries if e.get("id")]
    with _albums_lock:
        cache = _read_json(ALBUMS_FILE, {})
        hit = cache.get(pl_id)
        if hit and (hit.get("none") or all(e["id"] in hit.get("tracks", {}) for e in entries)):
            return None if hit.get("none") else hit
    guess = _guess_album(title, entries)
    album = None
    answered = True
    if guess:
        try:
            for g in _guesses(guess):
                album = _mb_release(g, entries)
                if album:
                    guess = g
                    break
        except Exception as exc:  # noqa: BLE001 — offline: decide from the titles alone, ask again next time
            print(f"[tubegrab] MusicBrainz lookup failed: {exc}")
            answered = False
        if not album and guess["said"] and guess["artist"]:
            album = {"artist": guess["artist"], "album": guess["album"], "year": guess["year"], "tracks": {}}
    if album:
        tracks = album["tracks"]
        taken = {t["no"] for t in tracks.values()}
        for i, e in enumerate(entries, 1):
            if e["id"] not in tracks:
                # not on the release (a bonus track…): its playlist position, when free
                tracks[e["id"]] = {"no": i if i not in taken and not album.get("mbid") else None,
                                   "title": _local_title(e.get("title") or "", guess["artist"], guess["album"])}
        album["guess_artist"] = guess["artist"]
        album["guess_album"] = guess["album"]
        album["url"] = url or f"https://www.youtube.com/playlist?list={pl_id}"
    if album or answered:
        with _albums_lock:
            cache = _read_json(ALBUMS_FILE, {})
            cache[pl_id] = album or {"none": True}
            _write_json(ALBUMS_FILE, cache)
    return album


def _album_for(url: str, opts: dict) -> dict | None:
    """Before an audio playlist downloads: is it one album? (only its titles are read)"""
    o = {k: opts[k] for k in ("js_runtimes", "cookiefile") if k in opts}
    o.update(quiet=True, no_warnings=True, extract_flat="in_playlist", ignoreerrors=True)
    try:
        with yt_dlp.YoutubeDL(o) as y:
            info = y.extract_info(url, download=False)
        if not info or info.get("_type") != "playlist" or _is_album_playlist({"playlist_id": info.get("id")}):
            return None
        entries = [{"id": e.get("id"), "title": e.get("title")} for e in info.get("entries") or [] if e]
        return _identify_album(info["id"], info.get("title") or "", entries, info.get("webpage_url"))
    except Exception as exc:  # noqa: BLE001 — never fail a download over this
        print(f"[tubegrab] album check failed: {exc}")
        return None


def _album_cover(key: str, album: dict) -> None:
    """The album's front cover from the Cover Art Archive, unless you chose one."""
    import types
    import urllib.request
    entry = _folders().get(key, {})
    folder = DOWNLOAD_DIR / key
    if not album.get("mbid") or entry.get("cover_file") or entry.get("cover") or not folder.is_dir():
        return
    data = None
    for path in (f"release/{album['mbid']}", f"release-group/{album['rgid']}" if album.get("rgid") else None):
        if not path:
            continue
        try:
            req = urllib.request.Request(f"{COVER_ART}/{path}/front-500",
                                         headers={"User-Agent": "TubeGrab/1.0 (self-hosted music library)"})
            with urllib.request.urlopen(req, timeout=30) as r:
                data = r.read(20 << 20)
            break
        except Exception:  # noqa: BLE001 — no cover there (404) or offline
            continue
    name = f"{uuid.uuid4().hex[:10]}.jpg"
    if not data or not _save_picture(types.SimpleNamespace(save=lambda p: Path(p).write_bytes(data)), COVERS_DIR / name):
        return
    with _meta_lock:
        folders = _folders()
        entry = folders.setdefault(key, {})
        if entry.get("cover_file") or entry.get("cover"):   # you picked one meanwhile
            (COVERS_DIR / name).unlink(missing_ok=True)
            return
        entry["cover_file"] = name
        _write_json(FOLDERS_FILE, folders)
    for p in folder.iterdir():
        if p.is_file() and p.suffix.lower() in AUDIO_EXTS:
            _embed_cover(p, COVERS_DIR / name)


# ---------------------------------------------------------------- metadata store

def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text("utf-8"))
    except Exception:  # noqa: BLE001
        return default


def _meta_changed(**changes) -> None:
    """Tell the library index something changed (it looks for changed folders, plus
    any rows / ids / folders named)."""
    ix = globals().get("_indexer")
    if ix is not None:
        ix.kick(**changes)


_last_json: dict[str, dict] = {}


def _diff_keys(old: dict, new: dict) -> set:
    return {k for k in old.keys() | new.keys() if old.get(k) != new.get(k)}


def _write_json(path: Path, data) -> None:
    old = None
    if path in (INDEX_FILE, FOLDERS_FILE):
        old = _last_json.get(path.name)
        if old is None:
            old = _read_json(path, {})
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
    tmp.replace(path)
    if path == INDEX_FILE:      # files that got (or lost) an id: rebuild their rows
        changed = _diff_keys(old, data)
        _last_json[path.name] = json.loads(json.dumps(data))
        _meta_changed(names=[n for k in changed for n in (old.get(k), data.get(k)) if n])
    elif path == FOLDERS_FILE:  # names, covers: rebuild those folders
        changed = _diff_keys(old, data)
        _last_json[path.name] = json.loads(json.dumps(data))
        _meta_changed(dirs=changed)
    elif path == PLAYLISTS_FILE:
        if "_lib_bump" in globals():
            _lib_bump()


_DL_PREFIX = str(DOWNLOAD_DIR) + os.sep


def _rel(path: Path) -> str:
    """Library-relative name. Paths built from the library folder are already
    absolute and clean: plain string work (resolve() is slow, and this runs per file)."""
    s = str(path)
    if s.startswith(_DL_PREFIX) and "/." not in s[len(_DL_PREFIX) - 1:]:
        return s[len(_DL_PREFIX):].replace(os.sep, "/")
    return path.resolve().relative_to(DOWNLOAD_DIR).as_posix()


def _kind(path: Path) -> str:
    return "audio" if path.suffix.lower() in AUDIO_EXTS else "video"


def _index() -> dict:
    return _read_json(INDEX_FILE, {})


def _index_set(vid: str, kind: str, rel: str) -> None:
    with _meta_lock:
        idx = _index()
        idx[f"{vid}:{kind}"] = rel
        _write_json(INDEX_FILE, idx)


def _index_drop(rel: str) -> None:
    with _meta_lock:
        idx = _index()
        for k in [k for k, v in idx.items() if v == rel]:
            idx.pop(k)
        _write_json(INDEX_FILE, idx)


def _index_move(old_prefix: str, new_prefix: str) -> None:
    """Re-point index entries after a file or folder moved."""
    with _meta_lock:
        idx = _index()
        for k, v in idx.items():
            if v == old_prefix or v.startswith(old_prefix + "/"):
                idx[k] = new_prefix + v[len(old_prefix):]
        _write_json(INDEX_FILE, idx)


def _ids_by_path() -> dict[str, str]:
    return {v: k.split(":")[0] for k, v in _index().items()}


def _video_id(path: Path, by_path: dict | None = None) -> str | None:
    rel = _rel(path)
    vid = (by_path if by_path is not None else _ids_by_path()).get(rel)
    if vid:
        return vid
    m = ID_RE.search(path.stem)
    return m.group(1) if m else None


def _folders() -> dict:
    return _read_json(FOLDERS_FILE, {})


def _note_folder(key: str, title: str | None, url: str | None) -> None:
    if not key or not (title or url):
        return
    with _meta_lock:
        folders = _folders()
        entry = folders.get(key, {})
        if title:
            entry.setdefault("title", title)
        if url:
            entry["url"] = url
        folders[key] = entry
        _write_json(FOLDERS_FILE, folders)


def _write_meta(info: dict, extra: dict | None = None) -> None:
    vid = info.get("id")
    if not vid:
        return
    keep = (
        "id", "title", "track", "artist", "album", "uploader", "channel", "channel_url",
        "upload_date", "release_year", "duration", "webpage_url", "view_count",
        "playlist_title", "playlist_id", "playlist_index", "chapters",
    )
    meta = {k: info.get(k) for k in keep if info.get(k) is not None}
    desc = info.get("description")
    if desc:
        meta["description"] = desc[:5000]
    meta.update({k: v for k, v in (extra or {}).items() if v is not None})
    meta["fetched_at"] = _now()
    path = META_DIR / f"{vid}.json"
    with _meta_lock:
        old = _read_json(path, {})
        for k, v in old.items():
            meta.setdefault(k, v)
        _write_json(path, meta)
    _meta_changed(walk=False, vids=[vid])


def _meta_for(vid: str | None) -> dict:
    return _read_json(META_DIR / f"{vid}.json", {}) if vid else {}


def _folder_cover(key: str, folders: dict | None = None) -> tuple[Path, str] | None:
    """A collection's custom cover: (image, cache token). An uploaded picture wins
    over an item the user picked."""
    entry = (folders if folders is not None else _folders()).get(key, {})
    up = entry.get("cover_file")
    if up and (COVERS_DIR / up).is_file():
        return COVERS_DIR / up, up
    vid = entry.get("cover")
    thumb = _meta_thumb(vid) if vid else None
    return (thumb, vid) if thumb else None


def _chosen_cover(path: Path, folders: dict | None = None) -> tuple[Path, str] | None:
    """The album cover the user set for this audio file's folder, if any."""
    if path.suffix.lower() not in AUDIO_EXTS:
        return None
    return _folder_cover(_rel(path.parent), folders)


def _meta_thumb(vid: str | None) -> Path | None:
    if not vid:
        return None
    for ext in THUMB_EXTS:
        p = META_DIR / f"{vid}{ext}"
        if p.exists() and p.stat().st_size > 0:
            return p
    return None


_TEMP_STEM = re.compile(r"\.(f\d+|temp)$")


def _walk_media():
    """(folder, filename, stem, ext) of every library media file, as plain strings
    (skips .meta, hidden dirs and yt-dlp temp files)."""
    for root, dirs, names in os.walk(DOWNLOAD_DIR):
        dirs[:] = [d for d in dirs if not d.startswith(".")]   # never descend into .meta
        for n in names:
            stem, ext = os.path.splitext(n)
            # skip pre-merge streams ("x.f399.mp4") and merge temp files ("x.temp.mp4")
            if ext.lower() in MEDIA_EXTS and not n.startswith(".") and not _TEMP_STEM.search(stem):
                yield root, n, stem, ext.lower()


def _iter_media():
    """All library media files."""
    for root, n, _, _ in _walk_media():
        yield Path(root, n)


def _find_existing(vid: str, kind: str) -> Path | None:
    rel = _index().get(f"{vid}:{kind}")
    if rel and (DOWNLOAD_DIR / rel).is_file():
        return DOWNLOAD_DIR / rel
    for path in _iter_media():               # legacy names with [id]
        m = ID_RE.search(path.stem)
        if m and m.group(1) == vid and _kind(path) == kind:
            return path
    return None


def _prune_empty_dirs(start: Path) -> None:
    d = start
    while d != DOWNLOAD_DIR and d.is_dir() and not any(d.iterdir()):
        d.rmdir()
        d = d.parent


def _unique(rel_noext: str, ext: str, vid: str | None) -> str:
    """Avoid clobbering a different video that happens to share a title."""
    owners = _ids_by_path()
    candidate, n = rel_noext, 2
    while True:
        existing = [p for p in (DOWNLOAD_DIR / candidate).parent.glob(f"{Path(candidate).name}.*")
                    if p.suffix.lower() in MEDIA_EXTS and _kind(p) == ("audio" if ext in AUDIO_EXTS else "video")]
        if not existing or all(owners.get(_rel(p)) == vid for p in existing):
            return candidate
        candidate, n = f"{rel_noext} ({n})", n + 1


def _keep_mtime(path: Path):
    """Restore a file's times after editing it in place (library sorts by mtime)."""
    st = path.stat()
    return lambda: os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))


def _retag(path: Path, tags: dict) -> None:
    """Rewrite audio tags (artist/album/year/track) in place."""
    if path.suffix.lower() not in AUDIO_EXTS:
        return
    try:
        restore = _keep_mtime(path)
        import mutagen
        f = mutagen.File(str(path), easy=True)
        if f is None:
            return
        for key, val in tags.items():
            if val is None:
                continue
            f[key] = str(val)
        f.save()
        restore()
    except Exception as exc:  # noqa: BLE001 — tags are cosmetic
        print(f"[tubegrab] retag failed for {path.name}: {exc}")


# ---------------------------------------------------------------- jobs

def _update(job_id: str, **fields) -> None:
    with _lock:
        _jobs[job_id].update(fields)
    _jobs_dirty.set()
    if "status" in fields:
        _meta_changed()   # a finished job: look for what it filed


def _add_file(job_id: str, path: str) -> None:
    _meta_changed()
    _jobs_dirty.set()
    with _lock:
        files = _jobs[job_id]["files"]
        if path not in files:
            files.append(path)


def _hook_for(job_id: str):
    """yt-dlp progress hook: streams percent/speed/ETA into the job record."""

    def hook(d):
        with _lock:
            cancelled = _jobs[job_id].get("cancel")
        if cancelled:
            raise yt_dlp.utils.DownloadCancelled()

        status = d.get("status")
        info = d.get("info_dict") or {}
        fields: dict = {}

        if not _jobs[job_id].get("title"):
            fields["title"] = (
                info.get("playlist_title") or info.get("title") or _jobs[job_id]["url"]
            )

        if status == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            done = d.get("downloaded_bytes") or 0
            fields.update(
                status="downloading",
                downloaded=done,
                total=total,
                speed=d.get("speed"),
                eta=d.get("eta"),
                current_file=Path(d.get("filename") or "").name or None,
                playlist_index=info.get("playlist_index"),
                playlist_count=info.get("playlist_count") or info.get("n_entries"),
            )
            if total:
                fields["percent"] = round(done / total * 100, 1)
            else:
                # fragmented downloads may not know the total size
                frag_count, frag_index = d.get("fragment_count"), d.get("fragment_index")
                fields["percent"] = (
                    round(frag_index / frag_count * 100, 1)
                    if frag_count and frag_index else None
                )

        with _lock:
            _jobs[job_id].update(fields)

    return hook


# Sponsor reads, "like & subscribe" and self-promotion are cut out of downloads
# using the community SponsorBlock database (it is sent a hashed id prefix only).
SPONSOR_CATEGORIES = ["sponsor", "selfpromo", "interaction"]


class _SafeSponsorBlockPP(SponsorBlockPP):
    """SponsorBlock that never fails a download (API down → keep the video uncut)."""

    def run(self, info):
        try:
            return super().run(info)
        except Exception as exc:  # noqa: BLE001
            self.report_warning(f"SponsorBlock unavailable, keeping the full video: {exc}")
            info["sponsorblock_chapters"] = []
            return [], info


class _SquareCoverPP(PostProcessor):
    """Music covers: YouTube shows square album art centred in a 16:9 frame;
    crop the thumbnail to its centre square before it's embedded."""

    def run(self, info):
        for t in info.get("thumbnails") or []:
            fp = t.get("filepath")
            if fp and os.path.exists(fp):
                _crop_square(Path(fp))
        return [], info


def _crop_square(path: Path) -> None:
    tmp = path.with_name(path.stem + ".sq" + path.suffix)
    try:
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(path),
             "-vf", "crop='min(iw,ih)':'min(iw,ih)'", "-q:v", "2", "-y", str(tmp)],
            timeout=30, check=True,
        )
        tmp.replace(path)
    except Exception:  # noqa: BLE001 — keep the uncropped cover
        tmp.unlink(missing_ok=True)


class _GrabDL(yt_dlp.YoutubeDL):
    """YoutubeDL with TubeGrab's library behaviour.

    * remembers the last extraction error (with `ignoreerrors` a failed
      extraction returns None and the reason only reaches report_error);
    * files every entry by _plan() — music, videos or playlists;
    * skips videos already in the library, moving a loose copy into the
      album/playlist being grabbed;
    * records metadata and the id → path index.
    """

    def __init__(self, params, *, job_id: str, playlist_mode: bool, kind: str, album: dict | None = None):
        super().__init__(params)
        self.album = album
        self.last_error: str | None = None
        self.job_id = job_id
        self.playlist_mode = playlist_mode
        self.media_kind = kind
        self.plans: dict[str, dict] = {}
        self._square = _SquareCoverPP(self)
        self.add_post_processor(_SafeSponsorBlockPP(self, categories=SPONSOR_CATEGORIES), when="after_filter")

    def report_error(self, message, *args, **kwargs):
        self.last_error = re.sub(r"^\s*ERROR:\s*", "", str(message)).strip()
        super().report_error(message, *args, **kwargs)

    def process_info(self, info_dict):
        audio = self.media_kind == "audio"
        plan = _plan(info_dict, audio=audio, playlist_mode=self.playlist_mode, album=self.album)
        vid = info_dict.get("id")
        ext = ".m4a" if audio else ".mp4"
        rel = _unique(plan["rel"], ext, vid)
        self.plans[vid] = plan
        _note_folder(str(Path(rel).parent.as_posix()), plan["collection_title"], plan["collection_url"])
        # music gets its tags from our plan (album, track number, album artist…)
        for k, v in plan["tags"].items():
            if v is not None:
                info_dict[k] = v   # "track" becomes the title tag
        if plan["tags"].get("release_year"):
            info_dict["meta_date"] = plan["tags"]["release_year"]   # date tag = release year
        self.params["outtmpl"]["default"] = str(DOWNLOAD_DIR / rel).replace("%", "%%") + ".%(ext)s"

        existing = _find_existing(vid, self.media_kind) if vid else None
        if existing:
            final = self._maybe_move(existing, rel, plan)
            _write_meta(info_dict, self._meta_extra(plan))
            if not _meta_thumb(vid):
                written = self._write_thumbnails("video", info_dict, self.prepare_filename(info_dict, "thumbnail"))
                if plan["section"] == "Music":
                    for fp, _ in written or []:
                        _crop_square(Path(fp))
            _index_set(vid, self.media_kind, _rel(final))
            _add_file(self.job_id, str(final))
            self.to_screen(f"[tubegrab] already in library: {_rel(final)}")
            return
        return super().process_info(info_dict)

    def pre_process(self, ie_info, key="pre_process", files_to_move=None):
        info, files_to_move = super().pre_process(ie_info, key, files_to_move)
        if key == "before_dl" and self.plans.get(info.get("id"), {}).get("section") == "Music":
            info = self.run_pp(self._square, info)
        return info, files_to_move

    @staticmethod
    def _meta_extra(plan: dict) -> dict:
        t = plan["tags"]
        return {"section": plan["section"], "album_artist": t.get("album_artist"),
                "album": t.get("album"), "release_year": t.get("release_year"),
                "track_number": t.get("track_number"),
                "track": t.get("track") if plan["section"] == "Music" else None,
                "extras": 1}   # chapters + captions fetched

    def _maybe_move(self, existing: Path, rel: str, plan: dict) -> Path:
        """Pull a loose copy into the album/playlist now being grabbed."""
        cur = _rel(existing)
        dest = DOWNLOAD_DIR / (rel + existing.suffix)
        if existing == dest or dest.exists() or _meta_for(_video_id(existing)).get("placed") == "user":
            return existing   # (you moved it there yourself: it stays)
        cur_section = cur.split("/")[0]
        legacy = cur_section not in SECTIONS
        if plan["section"] == "Music":
            # a loose single slots into its numbered album position
            ok = bool(plan["tags"].get("track_number")) and (
                legacy or (cur_section == "Music" and not INDEX_PREFIX_RE.match(existing.name)))
        elif plan["section"] == "Playlists":
            ok = legacy or cur_section == "Videos"   # never pull tracks out of albums
        else:
            ok = legacy
        if not ok:
            return existing
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(existing), str(dest))
        _prune_empty_dirs(existing.parent)
        _index_move(cur, _rel(dest))
        if plan["section"] == "Music":
            t = plan["tags"]
            _retag(dest, {"title": t.get("track"), "album": t.get("album"), "albumartist": t.get("album_artist"),
                          "tracknumber": t.get("track_number"), "date": t.get("release_year")})
        return dest


def _pp_hook_for(job_id: str, ydl_ref: dict):
    """Postprocessor hook: record the final (merged/extracted) file + metadata."""

    def hook(d):
        if d.get("status") != "finished":
            return
        info = d.get("info_dict") or {}
        final = info.get("filepath")
        if final and Path(final).suffix.lower() in MEDIA_EXTS and Path(final).exists():
            _add_file(job_id, final)
            plan = (ydl_ref.get("ydl").plans if ydl_ref.get("ydl") else {}).get(info.get("id"), {})
            _write_meta(info, _GrabDL._meta_extra(plan) if plan else None)
            if info.get("id"):
                _index_set(info["id"], _kind(Path(final)), _rel(Path(final)))
            if d.get("postprocessor") == "EmbedThumbnail":
                chosen = _chosen_cover(Path(final))
                if chosen and chosen[1] != info.get("id"):
                    _embed_cover(Path(final), chosen[0])
                if plan.get("section") == "Music":
                    _fetch_lyrics_bg(Path(final))

    return hook


SUB_LANGS = ["en", "en-US", "en-GB", "en-orig"]
SUB_OPTS = {"writesubtitles": True, "writeautomaticsub": True, "subtitleslangs": SUB_LANGS, "subtitlesformat": "vtt"}


def _base_opts(fmt: str) -> dict:
    opts: dict = {
        "format": FORMATS[fmt],
        # within the resolution cap prefer efficient codecs, then plain HTTPS
        # over HLS (HLS variants are often 1.5–2× bigger for the same picture)
        "format_sort": ["res", "fps", "vcodec", "proto"],
        "outtmpl": {
            "default": str(DOWNLOAD_DIR / "%(title)s.%(ext)s"),   # replaced per entry
            "thumbnail": str(META_DIR / "%(id)s.%(ext)s"),
            "pl_thumbnail": str(META_DIR / "%(id)s.%(ext)s"),
            "subtitle": str(META_DIR / "%(id)s.%(ext)s"),
        },
        "writethumbnail": True,
        "js_runtimes": JS_RUNTIMES,
        "ignoreerrors": True,      # one bad entry must not kill a playlist job
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "retries": 5,
        "concurrent_fragment_downloads": 4,
        "windowsfilenames": True,
    }
    cut = {"key": "ModifyChapters", "remove_sponsor_segments": SPONSOR_CATEGORIES}   # must run first
    if fmt == "audio":
        opts["postprocessors"] = [
            cut,
            # jpg is what every player understands for embedded cover art
            {"key": "FFmpegThumbnailsConvertor", "format": "jpg", "when": "before_dl"},
            # remux to .m4a; only transcodes (to AAC) if the source wasn't AAC already
            {"key": "FFmpegExtractAudio", "preferredcodec": "m4a"},
            {"key": "FFmpegMetadata", "add_metadata": True},   # title/artist/album/track tags
            # must run after tagging (ffmpeg's rewrite would drop it); keep the
            # copy in .meta/ for the library UI
            {"key": "EmbedThumbnail", "already_have_thumbnail": True},
        ]
    else:
        opts["merge_output_format"] = "mp4"
        opts.update(SUB_OPTS)   # captions → transcript, CC and search
        opts["postprocessors"] = [
            cut,
            {"key": "FFmpegMetadata", "add_metadata": False, "add_chapters": True},   # chapters for other players too
        ]
    if COOKIES_FILE.exists():
        opts["cookiefile"] = str(COOKIES_FILE)
    return opts


# Downloads run two at a time (TUBEGRAB_PARALLEL); the rest wait as "queued".
# More in parallel isn't faster on a small NAS: they share one CPU and one link.
_job_slots = threading.Semaphore(max(1, int(os.environ.get("TUBEGRAB_PARALLEL") or 2)))


def _run_job(job_id: str, url: str, fmt: str, playlist: bool) -> None:
    while not _job_slots.acquire(timeout=1):
        if _jobs[job_id].get("cancel"):
            _update(job_id, status="cancelled", finished_at=_now())
            return
    try:
        _run_job_slot(job_id, url, fmt, playlist)
    finally:
        _job_slots.release()
        _tidy_when_idle()


def _run_job_slot(job_id: str, url: str, fmt: str, playlist: bool) -> None:
    if _jobs[job_id].get("cancel"):
        _update(job_id, status="cancelled", finished_at=_now())
        return

    ref: dict = {}
    opts = _base_opts(fmt)
    opts.update(
        progress_hooks=[_hook_for(job_id)],
        postprocessor_hooks=[_pp_hook_for(job_id, ref)],
        noplaylist=not playlist,
    )

    try:
        album = _album_for(url, opts) if fmt == "audio" and playlist else None
        with _GrabDL(opts, job_id=job_id, playlist_mode=playlist,
                     kind="audio" if fmt == "audio" else "video", album=album) as ydl:
            ref["ydl"] = ydl
            info = ydl.extract_info(url, download=True)
        entries = (
            [e for e in (info.get("entries") or []) if e]
            if info and info.get("_type") == "playlist" else [info] if info else []
        )
        if not entries:
            raise RuntimeError(
                ydl.last_error or "Nothing was downloaded — check the URL."
            )
        if any(pl.get("mixed_playlist") for pl in ydl.plans.values()):
            _mirror_playlist(info, _jobs[job_id].get("files", []))
        if album:
            for key in {Path(p["rel"]).parent.as_posix() for p in ydl.plans.values() if p["section"] == "Music"}:
                _album_cover(key, album)
        _update(
            job_id,
            status="complete",
            title=info.get("playlist_title") or info.get("title") or url,
            percent=100.0, speed=None, eta=None, current_file=None,
            entry_count=len(entries), finished_at=_now(),
        )
    except yt_dlp.utils.DownloadCancelled:
        _update(job_id, status="cancelled", finished_at=_now())
    except Exception as exc:  # noqa: BLE001 — surface the message in the UI
        _update(job_id, status="error", error=str(exc), finished_at=_now())


def _mirror_playlist(info: dict, paths: list[str]) -> None:
    """Songs from a mixed YouTube playlist were filed into their albums; keep the
    playlist itself as one of your playlists (same name and order)."""
    names = [_rel(Path(p)) for p in paths
             if Path(p).is_file() and Path(p).suffix.lower() in MEDIA_EXTS]
    refs = _pl_refs(list(dict.fromkeys(names))) or []
    source = info.get("id") or info.get("webpage_url")
    if not refs or not source:
        return
    with _meta_lock:
        pls = _pl_all()
        pid = next((k for k, p in pls.items() if p.get("source") == source), None)
        if pid:   # downloaded again: add what's new, keep your order
            p = pls[pid]
            p["items"] = p.get("items", []) + [r for r in refs if r not in p.get("items", [])]
        else:
            pid = uuid.uuid4().hex[:10]
            p = pls[pid] = {"title": (info.get("title") or "Playlist")[:120], "description": "",
                            "items": refs, "cover": None, "created": _now(), "source": source}
        p["updated"] = _now()
        _write_json(PLAYLISTS_FILE, pls)


def _tidy_when_idle() -> None:
    """Tidy the music library once no download is writing into it."""
    with _lock:
        busy = any(j.get("status") in ("queued", "running", "downloading", "processing") for j in _jobs.values())
    if not busy:
        try:
            tidy_music()
            warm_thumbs()
        except Exception as exc:  # noqa: BLE001 — never fail a job over housekeeping
            print(f"[tubegrab] tidy failed: {exc}")


def serialize(job: dict) -> dict:
    public = {k: v for k, v in job.items() if k != "cancel"}
    out = []
    for f in public.get("files", []):
        if not Path(f).exists():   # intermediate streams yt-dlp already removed, or moved
            continue
        try:
            out.append(_rel(Path(f)))
        except ValueError:
            out.append(Path(f).name)
    public["files"] = out
    return public


# ---------------------------------------------------------------- library

def _safe_path(name: str, *, dir_ok: bool = False) -> Path | None:
    """Resolve a library path like 'Music/A/B/file.m4a', refusing anything
    outside downloads/ or inside hidden folders."""
    if not name or name.startswith("/") or "\\" in name:
        return None
    parts = name.split("/")
    if any(not p or p.startswith(".") for p in parts):
        return None
    path = (DOWNLOAD_DIR / name).resolve()
    try:
        path.relative_to(DOWNLOAD_DIR)
    except ValueError:
        return None
    if dir_ok:
        return path if path.is_dir() and path != DOWNLOAD_DIR else None
    return path if path.is_file() else None


def _display_title(name: str) -> str:
    """'03 - Song [dQw4w9WgXcQ].m4a' → 'Song'."""
    stem = Path(name).stem
    stem = re.sub(r"\s*\[[A-Za-z0-9_-]{11}\]$", "", stem) or stem
    return INDEX_PREFIX_RE.sub("", stem)


def _generate_thumb(path: Path, out: Path) -> None:
    """Grab a frame with ffmpeg; try 1s in, fall back to the first frame."""
    for seek in ("1", "0"):
        try:
            subprocess.run(
                ["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", seek,
                 "-i", str(path), "-frames:v", "1", "-vf", "scale=640:-2",
                 "-q:v", "4", "-y", str(out)],
                timeout=30, check=True,
            )
            if out.exists() and out.stat().st_size > 0:
                return
        except Exception:
            continue  # noqa: S110 — fall through to the placeholder


# ffprobe is slow on a NAS CPU: keep results on disk so restarts/deploys are instant
PROBE_FILE = META_DIR / "probe.json"
_probe_cache: dict[str, dict] = _read_json(PROBE_FILE, {})


def _probe(path: Path | str, st: os.stat_result | None = None) -> dict:
    """Duration + video height via ffprobe, cached per path+mtime."""
    st = st or os.stat(path)
    key = f"{path}|{int(st.st_mtime)}|{st.st_size}"
    if key in _probe_cache:
        return _probe_cache[key]
    res: dict = {"duration": None, "height": None}
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "format=duration:stream=height,codec_type:stream_disposition=attached_pic",
             "-of", "json", str(path)],
            capture_output=True, text=True, timeout=15,
        ).stdout
        data = json.loads(out or "{}")
        dur = (data.get("format") or {}).get("duration")
        res["duration"] = round(float(dur), 2) if dur else None
        for s in data.get("streams", []):
            if s.get("codec_type") == "video" and s.get("height") and not (s.get("disposition") or {}).get("attached_pic"):
                res["height"] = int(s["height"])
                break
    except Exception:  # noqa: BLE001 — cosmetic
        pass
    _probe_cache[key] = res
    return res


def _collection_info(key: str) -> dict:
    """Describe a collection folder from its path."""
    parts = key.split("/")
    section = parts[0] if parts[0] in SECTIONS and len(parts) >= 2 else "Other"
    name = parts[-1]
    info = {"key": key, "section": section, "artist": None, "year": None, "title": name}
    if section == "Music" and len(parts) >= 3:
        info["artist"] = parts[1]
        if name == SINGLES:
            info.update(kind="singles", title="Singles")
        else:
            m = YEAR_RE.search(name)
            info.update(kind="album", title=YEAR_RE.sub("", name), year=m.group(1) if m else None)
    elif section == "Videos":
        info["kind"] = "channel"
    elif section == "Playlists":
        info["kind"] = "playlist"
    else:
        info["kind"] = "folder"
    return info


def _clean_chapters(meta: dict) -> list[dict] | None:
    """Real chapters only (not the single 'whole video' one cutting sponsors leaves)."""
    ch = meta.get("chapters") or []
    if len(ch) < 2 or all(c.get("title") == meta.get("title") for c in ch):
        return None
    return [{"start": round(c.get("start_time") or 0, 2), "title": c.get("title") or ""} for c in ch]


# ---------------------------------------------------------------- library index
# The library is indexed in SQLite (.meta/library.db): one row per media file,
# one per collection, plus a full-text index of video captions. It is kept in
# step incrementally: folder mtimes say which folders changed (files added,
# removed, renamed or replaced), and metadata writes made by this process mark
# exactly the rows they affect. It is only an index — delete it and it is
# rebuilt from the files.

DB_FILE = META_DIR / "library.db"
DB_SCHEMA = 2
_dbl = threading.local()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS files(
  name TEXT PRIMARY KEY, dir TEXT NOT NULL, section TEXT NOT NULL, kind TEXT NOT NULL,
  title TEXT NOT NULL, sort_title TEXT NOT NULL, search TEXT NOT NULL,
  size INTEGER NOT NULL, mtime REAL NOT NULL, duration REAL, height INTEGER,
  vid TEXT, ref TEXT NOT NULL, artist TEXT, album TEXT, uploader TEXT, upload_date TEXT,
  year TEXT, url TEXT, idx INTEGER, art TEXT, tv TEXT NOT NULL, chapters TEXT,
  subs INTEGER NOT NULL DEFAULT 0, lyrics TEXT, fstamp TEXT NOT NULL, mstamp TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS files_dir ON files(dir);
CREATE INDEX IF NOT EXISTS files_section ON files(section, mtime);
CREATE INDEX IF NOT EXISTS files_ref ON files(ref);
CREATE INDEX IF NOT EXISTS files_vid ON files(vid);
CREATE INDEX IF NOT EXISTS files_mtime ON files(mtime);
CREATE TABLE IF NOT EXISTS collections(key TEXT PRIMARY KEY, section TEXT NOT NULL, updated REAL NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS dirs(path TEXT PRIMARY KEY, mtime INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS progress(ref TEXT PRIMARY KEY, pos REAL NOT NULL DEFAULT 0, watched INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS colors(tv TEXT PRIMARY KEY, color TEXT);
CREATE TABLE IF NOT EXISTS caption_state(name TEXT PRIMARY KEY, stamp TEXT NOT NULL, lo INTEGER, hi INTEGER);
CREATE VIRTUAL TABLE IF NOT EXISTS captions USING fts5(text, name UNINDEXED, start UNINDEXED, i UNINDEXED,
  tokenize='porter unicode61 remove_diacritics 2');
"""
_FILE_COLS = ("name", "dir", "section", "kind", "title", "sort_title", "search", "size", "mtime", "duration",
              "height", "vid", "ref", "artist", "album", "uploader", "upload_date", "year", "url", "idx", "art",
              "tv", "chapters", "subs", "lyrics", "fstamp", "mstamp")
_INSERT_FILE = f"INSERT OR REPLACE INTO files({','.join(_FILE_COLS)}) VALUES({','.join('?' * len(_FILE_COLS))})"


def _db() -> sqlite3.Connection:
    """This thread's connection (WAL: readers never wait for the indexer)."""
    con = getattr(_dbl, "con", None)
    if con is None:
        con = sqlite3.connect(DB_FILE, timeout=15, isolation_level=None, check_same_thread=False)
        con.row_factory = sqlite3.Row
        for p in ("journal_mode=WAL", "synchronous=NORMAL", "busy_timeout=15000", "temp_store=MEMORY", "cache_size=-16000"):
            con.execute(f"PRAGMA {p}")
        _dbl.con = con
    return con


class _tx:
    """BEGIN IMMEDIATE … COMMIT (or ROLLBACK on error)."""
    def __init__(self, con: sqlite3.Connection):
        self.con = con

    def __enter__(self):
        self.con.execute("BEGIN IMMEDIATE")
        return self.con

    def __exit__(self, exc_type, *_):
        self.con.execute("ROLLBACK" if exc_type else "COMMIT")
        return False


def _db_init() -> None:
    con = _db()
    if con.execute("PRAGMA user_version").fetchone()[0] != DB_SCHEMA:
        for t in ("files", "collections", "dirs", "progress", "colors", "caption_state", "captions"):
            con.execute(f"DROP TABLE IF EXISTS {t}")
        con.execute("VACUUM")
    con.executescript(_SCHEMA)
    con.execute(f"PRAGMA user_version={DB_SCHEMA}")
    _progress_to_db(_progress())


def _progress_to_db(prog: dict) -> None:
    """progress.json is the record; the table mirrors it so queries can join it."""
    con = _db()
    with _tx(con):
        con.execute("DELETE FROM progress")
        con.executemany("INSERT INTO progress VALUES(?,?,?)",
                        [(r, float(p.get("pos") or 0), 1 if p.get("watched") else 0) for r, p in prog.items()])


# -- change notifications: a version every client watches (see /api/events)

_bus = threading.Condition()
_bus_seq = 0
_bus_last: dict[str, tuple[int, str]] = {}          # latest event of each kind
_bus_prog: list[tuple[int, str]] = []               # recent progress patches (all of them matter)
_lib_version = 0


def _publish(kind: str, data) -> None:
    global _bus_seq
    payload = json.dumps(data, separators=(",", ":"))
    with _bus:
        _bus_seq += 1
        if kind == "progress":
            _bus_prog.append((_bus_seq, payload))
            del _bus_prog[:-200]
        else:
            _bus_last[kind] = (_bus_seq, payload)
        _bus.notify_all()


def _lib_bump() -> None:
    """Something clients show changed: they re-check what they have (ETags make
    unchanged answers free)."""
    global _lib_version
    _lib_version += 1
    _publish("lib", {"v": f"{_BOOT}-{_lib_version}"})


_BOOT = uuid.uuid4().hex[:6]


# -- the indexer: one background thread applies every change

class _Indexer:
    def __init__(self):
        self.wake = threading.Event()
        self.lock = threading.Lock()
        self.vids: set[str] = set()       # metadata of these ids changed
        self.names: set[str] = set()      # these rows need rebuilding
        self.dirs: set[str] = set()       # every row in these folders needs rebuilding
        self.walk = True                  # look for changed folders
        self.verify = True                # compare every row's metadata stamp (startup, hourly)
        self.ready = threading.Event()    # first full pass done
        self.known: dict[str, int] = {}   # folder → mtime_ns, mirrors the dirs table
        self.asked = 0                    # kicks so far / kicks a finished pass has covered
        self.covered = 0
        self.urgent = False
        self.done = threading.Condition()

    def kick(self, *, walk: bool = True, vids=(), names=(), dirs=()) -> int:
        with self.lock:
            self.walk = self.walk or walk
            self.vids.update(v for v in vids if v)
            self.names.update(n for n in names if n)
            self.dirs.update(d for d in dirs if d is not None)
            self.asked += 1
            n = self.asked
        self.wake.set()
        return n

    def flush(self, timeout: float = 8) -> None:
        """Apply pending changes now and wait for them (after an edit, so the
        answer the client gets next already shows it)."""
        if threading.current_thread().name == "indexer":
            return
        self.urgent = True               # (before the kick, so the indexer doesn't pause first)
        n = self.kick()
        with self.done:
            self.done.wait_for(lambda: self.covered >= n, timeout=timeout)

    def run(self) -> None:
        _db_init()
        self.known = {r[0]: r[1] for r in _db().execute("SELECT path, mtime FROM dirs")}
        last_verify = time.monotonic()
        while True:
            with self.lock:
                covers = self.asked
            try:
                self.sync()
            except Exception as exc:  # noqa: BLE001 — keep indexing
                import traceback
                traceback.print_exc()
                print(f"[tubegrab] index sync failed: {exc}")
            self.ready.set()
            with self.done:
                self.covered = covers
                self.done.notify_all()
            self.wake.wait(timeout=60)    # no news: look for outside changes (SMB, File Station) once a minute
            if not self.wake.is_set():
                with self.lock:
                    self.walk = True
                    if time.monotonic() - last_verify > 3600:
                        self.verify, last_verify = True, time.monotonic()
            self.wake.clear()
            for _ in range(6):            # let bursts of changes (a download landing) settle into one pass…
                if self.urgent:           # …unless an edit is waiting for it
                    break
                time.sleep(0.05)
            self.urgent = False

    def sync(self) -> None:
        with self.lock:
            walk, verify = self.walk, self.verify
            vids, names, force_dirs = self.vids, self.names, self.dirs
            self.walk = self.verify = False
            self.vids, self.names, self.dirs = set(), set(), set()
        con = _db()
        changed_dirs: set[str] = set(force_dirs)
        gone_dirs: set[str] = set()
        if walk:
            seen: dict[str, int] = {}
            base = len(str(DOWNLOAD_DIR)) + 1
            stack = [str(DOWNLOAD_DIR)]
            while stack:
                d = stack.pop()
                try:
                    mt = os.stat(d).st_mtime_ns
                    with os.scandir(d) as it:
                        stack.extend(e.path for e in it if not e.name.startswith(".") and e.is_dir(follow_symlinks=False))
                except OSError:
                    continue
                rel = d[base:].replace(os.sep, "/") if len(d) >= base else ""
                seen[rel] = mt
                if self.known.get(rel) != mt:
                    changed_dirs.add(rel)
            gone_dirs = set(self.known) - set(seen)
            new_known = seen
        else:
            new_known = None

        folders = _json_cached(FOLDERS_FILE)
        by_path = {v: k.split(":")[0] for k, v in _json_cached(INDEX_FILE).items()}
        covers: dict = {}
        upserts: list[dict] = []
        deletes: set[str] = set()
        touched: set[str] = set(gone_dirs)

        for d in gone_dirs:
            for (n,) in con.execute("SELECT name FROM files WHERE dir=?", (d,)):
                deletes.add(n)
        for d in changed_dirs:
            if d in gone_dirs:
                continue
            full = os.path.join(DOWNLOAD_DIR, d) if d else str(DOWNLOAD_DIR)
            have = {r["name"]: r for r in con.execute("SELECT * FROM files WHERE dir=?", (d,))}
            present = set()
            try:
                entries = list(os.scandir(full))
            except OSError:
                entries = []
            for e in entries:
                n = e.name
                stem, ext = os.path.splitext(n)
                if n.startswith(".") or ext.lower() not in MEDIA_EXTS or _TEMP_STEM.search(stem):
                    continue
                try:
                    if not e.is_file():
                        continue
                    st = e.stat()
                except OSError:
                    continue
                rel = f"{d}/{n}" if d else n
                present.add(rel)
                old = have.get(rel)
                if old is None or d in force_dirs or rel in names or old["fstamp"] != f"{st.st_mtime_ns}:{st.st_size}":
                    upserts.append(_build_row(e.path, rel, st, by_path, folders, covers, old))
            for rel in set(have) - present:
                deletes.add(rel)
            touched.add(d)

        # metadata changes (and periodic verification) for rows not rebuilt above
        done = {u["name"] for u in upserts} | deletes
        redo: set[str] = set(n for n in names if n not in done)
        if vids:
            vl = list(vids)
            for i in range(0, len(vl), 500):
                chunk = vl[i:i + 500]
                redo.update(r[0] for r in con.execute(
                    f"SELECT name FROM files WHERE vid IN ({','.join('?' * len(chunk))})", chunk))
        if verify:
            listing = set(os.listdir(META_DIR))
            for r in con.execute("SELECT name, vid, mstamp FROM files"):
                if r["name"] in done or not r["vid"]:
                    continue
                if _meta_stamp(r["vid"], listing) != r["mstamp"]:
                    redo.add(r["name"])
        for rel in redo - done:
            full = os.path.join(DOWNLOAD_DIR, rel)
            try:
                st = os.stat(full)
            except OSError:
                deletes.add(rel)
                continue
            old = con.execute("SELECT * FROM files WHERE name=?", (rel,)).fetchone()
            upserts.append(_build_row(full, rel, st, by_path, folders, covers, old))

        for u in upserts:
            touched.add(u["dir"])
        for n in deletes:
            touched.add(n.rpartition("/")[0])
        dirs_moved = new_known is not None and new_known != self.known
        if not upserts and not deletes and not dirs_moved and not force_dirs:
            return
        coll_changed = False

        with _tx(con):
            if deletes:
                dl = list(deletes)
                for i in range(0, len(dl), 500):
                    chunk = dl[i:i + 500]
                    q = ",".join("?" * len(chunk))
                    con.execute(f"DELETE FROM files WHERE name IN ({q})", chunk)
                    for lo, hi in con.execute(f"SELECT lo, hi FROM caption_state WHERE name IN ({q}) AND lo IS NOT NULL", chunk).fetchall():
                        con.execute("DELETE FROM captions WHERE rowid BETWEEN ? AND ?", (lo, hi))
                    con.execute(f"DELETE FROM caption_state WHERE name IN ({q})", chunk)
            con.executemany(_INSERT_FILE, [tuple(u[c] for c in _FILE_COLS) for u in upserts])
            for key in touched:
                if not key:
                    continue
                data = _collection_data(con, key, folders)
                old = con.execute("SELECT data FROM collections WHERE key=?", (key,)).fetchone()
                if data is None:
                    if old:
                        con.execute("DELETE FROM collections WHERE key=?", (key,))
                        coll_changed = True
                    continue
                blob = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
                if not old or old[0] != blob:
                    con.execute("INSERT OR REPLACE INTO collections VALUES(?,?,?,?)", (key, data["section"], data["updated"], blob))
                    coll_changed = True
            if new_known is not None:
                for d in gone_dirs:
                    con.execute("DELETE FROM dirs WHERE path=?", (d,))
                con.executemany("INSERT OR REPLACE INTO dirs VALUES(?,?)",
                                [(d, m) for d, m in new_known.items() if self.known.get(d) != m])
        if new_known is not None:
            self.known = new_known
        videos = [u for u in upserts if u["kind"] == "video"]
        for i in range(0, len(videos), 40):   # short transactions: progress writes never wait long
            with _tx(con):
                for u in videos[i:i + 40]:
                    _index_captions(con, u["name"], u["vid"] if u["subs"] else None)
        if upserts or deletes or coll_changed:
            _lib_bump()


_indexer = _Indexer()
_jc: dict[str, tuple[int, dict]] = {}


def _json_cached(path: Path) -> dict:
    """index.json / folders.json for the indexer: parsed again only when they change."""
    try:
        mt = path.stat().st_mtime_ns
    except OSError:
        return {}
    hit = _jc.get(str(path))
    if hit and hit[0] == mt:
        return hit[1]
    data = _read_json(path, {})
    _jc[str(path)] = (mt, data)
    return data


def _meta_stamp(vid: str | None, listing: set[str] | None = None) -> str:
    """Changes when anything a row shows from .meta changes: the metadata file, and
    whether captions / lyrics exist."""
    if not vid:
        return ""
    try:
        st = os.stat(f"{META_DIR}/{vid}.json")
        j = f"{st.st_mtime_ns}:{st.st_size}"
    except OSError:
        j = ""
    if listing is not None:
        has = lambda s: f"{vid}{s}" in listing   # noqa: E731
    else:
        has = lambda s: os.path.exists(f"{META_DIR}/{vid}{s}")   # noqa: E731
    flags = "".join("1" if has(sfx) else "0" for sfx in (".lrc", ".lyrics.txt", *(f".{lang}.vtt" for lang in SUB_LANGS)))
    return f"{j}|{flags}"


def _build_row(full: str, rel: str, st: os.stat_result, by_path: dict, folders: dict, covers: dict,
               old: sqlite3.Row | None) -> dict:
    fname = rel.rpartition("/")[2]
    stem, ext = os.path.splitext(fname)
    kind = "audio" if ext.lower() in AUDIO_EXTS else "video"
    m = ID_RE.search(stem)
    vid = by_path.get(rel) or (m.group(1) if m else None)
    meta = _meta_for(vid)
    fstamp = f"{st.st_mtime_ns}:{st.st_size}"
    if old is not None and old["fstamp"] == fstamp and old["duration"] is not None:
        duration, height = old["duration"], old["height"]   # same file: no need to ffprobe again
    else:
        probe = _probe(full, st)
        duration, height = probe["duration"] or meta.get("duration"), probe["height"]
    key = rel.rpartition("/")[0]
    c = _collection_info(key) if key else {"section": "Other", "artist": None}
    art = None
    src = None
    if c["section"] == "Music" and kind == "audio" and key:
        if key not in covers:
            covers[key] = _folder_cover(key, folders)
        if covers[key]:
            src, art = covers[key]
    if src is None and vid:
        src = _meta_thumb(vid)
    try:
        s2 = src.stat() if src else None
        basis = f"{src}|{s2.st_mtime_ns}|{s2.st_size}" if s2 else f"{rel}|{fstamp}"
    except OSError:
        basis = f"{rel}|{fstamp}"
    title = meta.get("track") or meta.get("title") or _display_title(fname)
    artist = meta.get("artist") or c.get("artist") or _clean_channel(meta.get("uploader"))
    uploader = _clean_channel(meta.get("uploader") or meta.get("channel"))
    album = meta.get("album")
    mi = INDEX_PREFIX_RE.match(fname)
    ch = _clean_chapters(meta)
    lyrics = None
    if vid and kind == "audio":
        lyrics = ("synced" if os.path.exists(f"{META_DIR}/{vid}.lrc")
                  else "plain" if os.path.exists(f"{META_DIR}/{vid}.lyrics.txt") else None)
    return {
        "name": rel, "dir": key, "section": c["section"], "kind": kind,
        "title": title, "sort_title": title.casefold(),
        "search": " ".join(x for x in (title, artist, album, uploader, key) if x).casefold(),
        "size": st.st_size, "mtime": st.st_mtime, "duration": duration,
        "height": height if kind == "video" else None,
        "vid": vid, "ref": f"{vid}:{kind}" if vid else f"path:{rel}",
        "artist": artist, "album": album, "uploader": uploader, "upload_date": meta.get("upload_date"),
        "year": meta.get("release_year"), "url": meta.get("webpage_url"),
        "idx": int(mi.group(1)) if mi else None, "art": art,
        "tv": hashlib.sha1(basis.encode()).hexdigest()[:10],
        "chapters": json.dumps(ch) if ch else None,
        "subs": 1 if vid and _sub_file(vid) else 0, "lyrics": lyrics,
        "fstamp": fstamp, "mstamp": _meta_stamp(vid),
    }


def _collection_data(con: sqlite3.Connection, key: str, folders: dict) -> dict | None:
    rows = [dict(r) for r in con.execute(
        "SELECT name, title, idx, size, duration, mtime, vid, album, tv FROM files WHERE dir=?", (key,))]
    if not rows:
        return None
    c = _collection_info(key)
    extra = folders.get(key, {})
    named = bool(extra.get("title")) and c["kind"] != "singles"
    if named:
        c["title"] = extra["title"]   # YouTube's original name, or the user's edit
    if c["kind"] in ("album", "playlist"):
        rows.sort(key=lambda f: (f["idx"] is None, f["idx"] or 0, f["title"].lower()))
    else:
        rows.sort(key=lambda f: -f["mtime"])
    c.update(url=extra.get("url"), count=len(rows), size=sum(f["size"] for f in rows),
             duration=sum(f["duration"] or 0 for f in rows), updated=max(f["mtime"] for f in rows),
             first=rows[0]["name"], art=None, cover_url=None)
    shown = rows
    custom = _folder_cover(key, folders)
    if custom:
        c["art"] = custom[1]
        if custom[0].parent == COVERS_DIR:
            c["cover_url"] = f"/collection-cover/{custom[1]}"
        else:   # an item's thumbnail: put that item first
            shown = sorted(rows, key=lambda f: f["vid"] != custom[1])
    c["covers"] = [{"name": f["name"], "tv": f["tv"]} for f in shown[:3]]
    if c["kind"] == "album" and not named:
        # real album name from tags ("Global Entry: Deluxe"), not the filename-safe folder
        albums = [f["album"] for f in rows if f.get("album")]
        if albums:
            c["title"] = max(set(albums), key=albums.count)
    return c


def _index_captions(con: sqlite3.Connection, name: str, vid: str | None) -> None:
    """(Re)index a video's captions for search, if they changed. Runs inside the
    caller's transaction. A video's lines are stored together, so its old ones go
    by rowid range (the text index can't look rows up by name)."""
    sub = _sub_file(vid) if vid else None
    stamp = ""
    if sub:
        st = sub.stat()
        stamp = f"{sub.name}|{st.st_mtime_ns}|{st.st_size}"
    row = con.execute("SELECT stamp, lo, hi FROM caption_state WHERE name=?", (name,)).fetchone()
    if row is not None and row["stamp"] == stamp:
        return
    if row is None and not stamp:
        con.execute("INSERT OR REPLACE INTO caption_state VALUES(?,?,NULL,NULL)", (name, ""))
        return
    if row is not None and row["lo"] is not None:
        con.execute("DELETE FROM captions WHERE rowid BETWEEN ? AND ?", (row["lo"], row["hi"]))
    segs = []
    if sub:
        try:
            segs = _parse_vtt(sub.read_text("utf-8", errors="replace"))
        except Exception:  # noqa: BLE001
            segs = []
    rows = []
    for k, sg in enumerate(segs):
        # a line plus the start of the next, so phrases split across caption lines still match
        nxt = " ".join(segs[k + 1]["text"].split()[:8]) if k + 1 < len(segs) else ""
        rows.append(((sg["text"] + " " + nxt).strip(), name, sg["start"], k))
    lo = hi = None
    if rows:
        top = con.execute("SELECT COALESCE(MAX(rowid), 0) FROM captions").fetchone()[0]
        lo, hi = top + 1, top + len(rows)
        con.executemany("INSERT INTO captions(rowid, text, name, start, i) VALUES(?,?,?,?,?)",
                        [(lo + n, *r) for n, r in enumerate(rows)])
    con.execute("INSERT OR REPLACE INTO caption_state VALUES(?,?,?,?)", (name, stamp, lo, hi))


# -- reading the index

_OUT_COLS = ("f.name, f.dir, f.section, f.kind, f.title, f.size, f.mtime, f.duration, f.height, f.vid, f.artist, "
             "f.album, f.uploader, f.upload_date, f.year, f.url, f.idx, f.art, f.tv, f.subs, f.lyrics, k.color")


def _file_out(r: sqlite3.Row, chapters: str | None = None) -> dict:
    out = {
        "name": r["name"], "title": r["title"], "kind": r["kind"], "size": r["size"], "mtime": r["mtime"],
        "duration": r["duration"], "height": r["height"], "section": r["section"], "collection": r["dir"],
        "id": r["vid"], "artist": r["artist"], "album": r["album"], "uploader": r["uploader"],
        "upload_date": r["upload_date"], "year": r["year"], "url": r["url"], "index": r["idx"],
        "art": r["art"], "tv": r["tv"], "color": r["color"], "subs": bool(r["subs"]), "lyrics": r["lyrics"],
    }
    if chapters is not None:
        out["chapters"] = json.loads(chapters) if chapters else None
    return out


def _rows_by_names(con: sqlite3.Connection, names: list[str]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for i in range(0, len(names), 500):
        chunk = names[i:i + 500]
        for r in con.execute(f"SELECT {_OUT_COLS} FROM files f LEFT JOIN colors k ON k.tv=f.tv "
                             f"WHERE f.name IN ({','.join('?' * len(chunk))})", chunk):
            out[r["name"]] = _file_out(r)
    return out


def _order_sql(kind: str) -> str:
    return ("f.idx IS NULL, f.idx, f.sort_title" if kind in ("album", "playlist") else "f.mtime DESC")


def _cached_json(body: bytes, etag: str | None = None, gz: bytes | None = None) -> Response:
    """JSON with an ETag (the body's hash unless given): unchanged → 304, no body."""
    etag = etag or hashlib.sha1(body).hexdigest()[:20]
    if request.if_none_match.contains(etag):
        resp = Response(status=304)
    else:
        zipped = "gzip" in (request.headers.get("Accept-Encoding") or "") and len(body) > 1024
        if zipped and gz is None:
            import gzip
            gz = gzip.compress(body, 5)
        resp = Response(gz if zipped else body, mimetype="application/json")
        if zipped:
            resp.headers["Content-Encoding"] = "gzip"
        resp.headers.add("Vary", "Accept-Encoding")
    resp.set_etag(etag)
    resp.headers["Cache-Control"] = "no-cache"
    return resp


def _json_bytes(data) -> bytes:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode()


_lib_cache: dict = {"key": None, "body": b"", "gz": b"", "etag": ""}


@app.get("/api/library")
def api_library():
    """Collections (every album, channel, playlist folder) + totals: small enough to
    send whole, so grids, artists and sorting work instantly on the device."""
    key = _lib_version
    c = _lib_cache
    if c["key"] != key:
        con = _db()
        colors = dict(con.execute("SELECT tv, color FROM colors WHERE color IS NOT NULL").fetchall())
        cols = []
        for (data,) in con.execute("SELECT data FROM collections ORDER BY updated DESC"):
            d = json.loads(data)
            for cv in d["covers"]:
                cv["color"] = colors.get(cv["tv"])
            cols.append(d)
        watched = dict(con.execute(
            "SELECT f.dir, COUNT(*) FROM files f JOIN progress p ON p.ref=f.ref "
            "WHERE p.watched=1 AND f.kind='video' AND f.dir!='' GROUP BY f.dir").fetchall())
        stats = {"music": {"count": 0, "size": 0, "duration": 0, "audio": 0},
                 "videos": {"count": 0, "size": 0, "duration": 0, "audio": 0}}
        for r in con.execute("SELECT section='Music' AS m, COUNT(*), COALESCE(SUM(size),0), "
                             "COALESCE(SUM(duration),0), SUM(kind='audio') FROM files GROUP BY m"):
            stats["music" if r[0] else "videos"] = {"count": r[1], "size": r[2], "duration": r[3], "audio": r[4] or 0}
        body = _json_bytes({"dir": str(DOWNLOAD_DIR), "ready": _indexer.ready.is_set(),
                            "collections": cols, "watched": watched, "stats": stats})
        import gzip
        c.update(key=key, body=body, gz=gzip.compress(body, 5), etag=hashlib.sha1(body).hexdigest()[:20])
    return _cached_json(c["body"], c["etag"], c["gz"])


@app.get("/api/files")
def api_files():
    """Library files, filtered / sorted / paged in the database.
    dir=<collection> · section=Music|videos · kind=audio|video · q=<words>
    sort=new|old|title|size|long|auto · offset · limit · only=names"""
    a = request.args
    where, args = ["1=1"], []
    d = a.get("dir")
    if d is not None:
        where.append("f.dir=?")
        args.append(d)
    if a.get("section") == "Music":
        where.append("f.section='Music'")
    elif a.get("section") == "videos":
        where.append("f.section!='Music'")
    if a.get("kind") in ("audio", "video"):
        where.append("f.kind=?")
        args.append(a["kind"])
    for tok in (a.get("q") or "").casefold().split()[:8]:
        where.append("f.search LIKE ? ESCAPE '\\'")
        args.append("%" + tok.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
    sort = a.get("sort") or "auto"
    orders = {"new": "f.mtime DESC", "old": "f.mtime ASC", "title": "f.sort_title", "size": "f.size DESC",
              "long": "COALESCE(f.duration,0) DESC"}
    order = orders.get(sort) or (_order_sql(_collection_info(d)["kind"]) if d else "f.mtime DESC")
    w = " AND ".join(where)
    con = _db()
    if a.get("only") == "names":
        names = [r[0] for r in con.execute(f"SELECT f.name FROM files f WHERE {w} ORDER BY {order}, f.name LIMIT 20000", args)]
        return _cached_json(_json_bytes({"names": names}))
    try:
        limit = max(1, min(int(a.get("limit") or 60), 2000))
        offset = max(0, int(a.get("offset") or 0))
    except ValueError:
        return jsonify(error="Bad paging."), 400
    total = con.execute(f"SELECT COUNT(*) FROM files f WHERE {w}", args).fetchone()[0]
    rows = con.execute(f"SELECT {_OUT_COLS} FROM files f LEFT JOIN colors k ON k.tv=f.tv WHERE {w} "
                       f"ORDER BY {order}, f.name LIMIT ? OFFSET ?", [*args, limit, offset]).fetchall()
    return _cached_json(_json_bytes({"total": total, "files": [_file_out(r) for r in rows]}))


@app.get("/api/watch/<path:name>")
def api_watch(name: str):
    """One file plus what plays around it: the rest of its playlist (?list=) or
    album/collection, then the newest from the same section."""
    con = _db()
    r = con.execute(f"SELECT {_OUT_COLS}, f.chapters FROM files f LEFT JOIN colors k ON k.tv=f.tv WHERE f.name=?",
                    (name,)).fetchone()
    if r is None:
        return jsonify(error="File not found."), 404
    f = _file_out(r, r["chapters"] or "")
    crow = con.execute("SELECT data FROM collections WHERE key=?", (f["collection"],)).fetchone() if f["collection"] else None
    coll = json.loads(crow[0]) if crow else None
    out: dict = {"file": f, "collection": coll, "playlist": None, "prev": None, "next": []}
    pid = request.args.get("list")
    pl = _pl_all().get(pid) if pid else None
    if pl is not None:
        idx = _index()
        names = [n for n in (_pl_resolve(ref, idx) for ref in pl.get("items", [])) if n]
        if name in names:
            i = names.index(name)
            rows = _rows_by_names(con, names[max(0, i - 1):i + 201])
            out["playlist"] = {"id": pid, "title": pl.get("title") or "Untitled", "position": i, "total": len(names)}
            out["next"] = [rows[n] for n in names[i + 1:i + 201] if n in rows]
            out["prev"] = rows.get(names[i - 1]) if i > 0 else None
            return _cached_json(_json_bytes(out))
    after: list[dict] = []
    if f["collection"]:
        kind = coll["kind"] if coll else _collection_info(f["collection"])["kind"]
        members = [x["name"] for x in con.execute(
            f"SELECT f.name FROM files f WHERE f.dir=? ORDER BY {_order_sql(kind)}, f.name", (f["collection"],))]
        i = members.index(name) if name in members else -1
        want = members[i + 1:i + 25] + ([members[i - 1]] if i > 0 and kind in ("album", "playlist") else [])
        rows = _rows_by_names(con, want)
        after = [rows[n] for n in members[i + 1:i + 25] if n in rows]
        if i > 0 and kind in ("album", "playlist"):
            out["prev"] = rows.get(members[i - 1])
    if len(after) < 24:
        music = f["section"] == "Music"
        rest = con.execute(
            f"SELECT {_OUT_COLS} FROM files f LEFT JOIN colors k ON k.tv=f.tv WHERE f.dir!=? AND f.name!=? "
            f"AND (f.section='Music')=? ORDER BY f.mtime DESC LIMIT ?",
            (f["collection"], name, 1 if music else 0, 24 - len(after))).fetchall()
        after += [_file_out(x) for x in rest]
    out["next"] = after
    return _cached_json(_json_bytes(out))


@app.get("/api/random")
def api_random():
    """A random item from a collection (dir=), an artist (artist=) or one of your playlists (list=)."""
    a, con = request.args, _db()
    row = None
    if a.get("dir"):
        row = con.execute("SELECT name FROM files WHERE dir=? ORDER BY RANDOM() LIMIT 1", (a["dir"],)).fetchone()
    elif a.get("artist"):
        pat = "Music/" + a["artist"].replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "/%"
        row = con.execute("SELECT name FROM files WHERE dir LIKE ? ESCAPE '\\' ORDER BY RANDOM() LIMIT 1", (pat,)).fetchone()
    elif a.get("list"):
        pl = _pl_all().get(a["list"])
        if pl:
            idx = _index()
            names = [n for n in (_pl_resolve(ref, idx) for ref in pl.get("items", [])) if n]
            if names:
                import random
                return jsonify(name=random.choice(names))
    return jsonify(name=row[0] if row else None)


@app.get("/api/events")
def api_events():
    """Server-sent events: library changes ("lib"), progress made on other devices
    ("progress") and download status ("jobs"). Replaces polling."""
    def stream():
        with _bus:
            last = _bus_seq
        yield "retry: 3000\n\n"
        yield f"event: lib\ndata: {json.dumps({'v': f'{_BOOT}-{_lib_version}'})}\n\n"
        yield f"event: jobs\ndata: {_jobs_payload()}\n\n"
        while True:
            with _bus:
                _bus.wait_for(lambda: _bus_seq > last, timeout=15)
                seq = _bus_seq
                pending = sorted([(s, k, d) for k, (s, d) in _bus_last.items() if s > last]
                                 + [(s, "progress", d) for s, d in _bus_prog if s > last])
                last = seq
            if not pending:
                yield ": ping\n\n"   # keeps proxies from closing an idle stream
                continue
            for _, kind, data in pending:
                yield f"event: {kind}\ndata: {data}\n\n"
    resp = Response(stream(), mimetype="text/event-stream")
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["X-Accel-Buffering"] = "no"
    return resp


def _jobs_payload() -> str:
    with _lock:
        jobs = [serialize(j) for j in _jobs.values()]
    jobs.sort(key=lambda j: j["created_at"], reverse=True)
    return json.dumps(jobs, separators=(",", ":"))


_jobs_dirty = threading.Event()


def _jobs_publisher() -> None:
    """Download progress changes many times a second; send it at most once a second."""
    while True:
        _jobs_dirty.wait()
        _jobs_dirty.clear()
        with _bus:
            global _bus_seq
            _bus_seq += 1
            _bus_last["jobs"] = (_bus_seq, _jobs_payload())
            _bus.notify_all()
        time.sleep(1)


_warming = threading.Lock()


def warm_thumbs() -> None:
    """Grid-sized copies + a placeholder colour for every thumbnail, ahead of time."""
    if not _warming.acquire(blocking=False):
        return
    try:
        _warm_thumbs()
    finally:
        _warming.release()


def _warm_thumbs() -> None:
    con = _db()
    todo = con.execute("SELECT f.name, f.tv FROM files f LEFT JOIN colors k ON k.tv=f.tv "
                       "WHERE k.tv IS NULL GROUP BY f.tv").fetchall()
    done = 0
    for r in todo:
        path = DOWNLOAD_DIR / r["name"]
        if not path.is_file():
            continue
        src = _thumb_image(path)
        color = _small(src, color=True, background=True)[1] if src else None
        con.execute("INSERT OR REPLACE INTO colors VALUES(?,?)", (r["tv"], color))
        done += 1
        if done % 200 == 0:
            _lib_bump()
    for p in [*COVERS_DIR.glob("*.jpg"), *PL_COVERS_DIR.glob("*.jpg")]:
        _small(p)
    if done:
        _lib_bump()


def _image_color(img: Path, background: bool = False) -> str | None:
    """Average colour of an image, as #rrggbb (shown while the picture loads)."""
    try:
        out = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(img), "-frames:v", "1",
                              "-vf", "scale=1:1:flags=area", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"],
                             capture_output=True, timeout=15,
                             preexec_fn=(lambda: os.nice(15)) if background else None).stdout
        return "#" + out[:3].hex() if len(out) >= 3 else None
    except Exception:  # noqa: BLE001
        return None


def _placeholder(audio: bool = False):
    return send_file(PLACEHOLDER_AUDIO if audio else PLACEHOLDER, mimetype="image/svg+xml", max_age=3600)


def _embedded_art(path: Path) -> Path | None:
    """Cover art embedded in an audio file (e.g. audio files from elsewhere)."""
    st = path.stat()
    key = hashlib.sha1(f"{path}|{int(st.st_mtime)}|art".encode()).hexdigest()[:16]
    out = THUMBS_DIR / f"{key}.jpg"
    if not out.exists():
        try:
            subprocess.run(
                ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(path),
                 "-an", "-frames:v", "1", "-y", str(out)],
                timeout=20, check=True,
            )
        except Exception:  # noqa: BLE001 — no embedded art
            out.unlink(missing_ok=True)
            return None
    return out if out.exists() and out.stat().st_size else None


SMALL_W = 480                          # grid cards are ≤ 240 css px wide → sharp on 2× screens
_small_slots = threading.Semaphore(2)  # don't let a fresh grid start 50 ffmpegs on the NAS


def _small(src: Path, color: bool = False, background: bool = False):
    """A ≤ 480 px wide JPEG copy of `src` (made once, cached); `src` if it already is
    small. With `color`: (copy, average colour "#rrggbb") — one ffmpeg run for both."""
    def done(path: Path, rgb: bytes | None = None):
        if not color:
            return path
        return path, ("#" + rgb[:3].hex() if rgb and len(rgb) >= 3 else _image_color(path, background))
    try:
        st = src.stat()
    except OSError:
        return done(src, b"") if color else src
    if st.st_size < 30_000:
        return done(src)
    out = SMALL_DIR / (hashlib.sha1(f"{src}|{st.st_mtime_ns}|{st.st_size}".encode()).hexdigest()[:20] + ".jpg")
    if out.exists():
        return done(out)
    with _small_slots:
        if out.exists():
            return done(out)
        tmp = SMALL_DIR / f".{uuid.uuid4().hex}.jpg"
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(src), "-frames:v", "1",
               "-vf", f"scale='min({SMALL_W},iw)':-2", "-q:v", "5", "-y", str(tmp)]
        if color:   # second output: the picture shrunk to one averaged pixel
            cmd += ["-frames:v", "1", "-vf", "scale=1:1:flags=area", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"]
        try:
            r = subprocess.run(cmd, timeout=20, check=True, capture_output=True,
                               preexec_fn=(lambda: os.nice(15)) if background else None)
            if not tmp.stat().st_size:
                raise OSError("empty")
            tmp.replace(out)
        except Exception:  # noqa: BLE001 — serve the original
            tmp.unlink(missing_ok=True)
            return done(src)
    return done(out, r.stdout if color else None)


def _send_image(src: Path, max_age: int = 86400, mimetype: str | None = None):
    """An image, grid-sized unless ?full=1. Versioned URLs (?v=…) never change, so
    browsers keep them for a year without asking again."""
    out = src if request.args.get("full") else _small(src)
    resp = send_file(out, mimetype="image/jpeg" if out != src else mimetype, max_age=max_age)
    if max_age >= 31536000:
        resp.cache_control.immutable = True
    return resp


def _thumb_image(path: Path, own: bool = False) -> Path | None:
    """The picture behind a file's thumbnail: the album cover you chose, YouTube's
    thumbnail, embedded art, or a frame from the video."""
    chosen = None if own else _chosen_cover(path)
    yt = chosen[0] if chosen else _meta_thumb(_video_id(path))
    if yt:
        return yt
    if path.suffix.lower() in AUDIO_EXTS:
        return _embedded_art(path)
    st = path.stat()
    out = THUMBS_DIR / (hashlib.sha1(f"{_rel(path)}|{int(st.st_mtime)}".encode()).hexdigest()[:16] + ".jpg")
    if not out.exists():
        _generate_thumb(path, out)
    return out if out.exists() else None


def _embed_cover(path: Path, jpg: Path) -> None:
    """Replace the embedded cover of an .m4a with `jpg`."""
    if path.suffix.lower() != ".m4a" or not jpg.exists():
        return
    try:
        from mutagen.mp4 import MP4, MP4Cover
        restore = _keep_mtime(path)
        f = MP4(str(path))
        f["covr"] = [MP4Cover(jpg.read_bytes(), imageformat=MP4Cover.FORMAT_JPEG)]
        f.save()
        restore()
    except Exception as exc:  # noqa: BLE001
        print(f"[tubegrab] cover embed failed for {path.name}: {exc}")


def _legacy_files() -> list[Path]:
    """Files not yet in the Music/Videos/Playlists layout."""
    return [p for p in _iter_media() if _rel(p).split("/")[0] not in SECTIONS or "/" not in _rel(p)]


def migrate_legacy() -> None:
    """Re-file anything saved with the old layout ('<Folder>/NNN - Title [id].ext'),
    using fresh YouTube metadata plus the playlist context we recorded then."""
    legacy = _legacy_files()
    if not legacy:
        return
    print(f"[tubegrab] migrating {len(legacy)} file(s) to the Music/Videos/Playlists layout…")
    opts = {"quiet": True, "no_warnings": True, "skip_download": True, "js_runtimes": JS_RUNTIMES,
            "writethumbnail": True, "outtmpl": {"thumbnail": str(META_DIR / "%(id)s.%(ext)s")},
            "postprocessors": [{"key": "FFmpegThumbnailsConvertor", "format": "jpg", "when": "before_dl"}]}
    if COOKIES_FILE.exists():
        opts["cookiefile"] = str(COOKIES_FILE)
    old_folders = _folders()
    with yt_dlp.YoutubeDL(opts) as ydl:
        for path in legacy:
            m = ID_RE.search(path.stem)
            if not m:
                print(f"[tubegrab]   skip (no video id): {_rel(path)}")
                continue
            vid, kind = m.group(1), _kind(path)
            old = _meta_for(vid)
            try:
                info = ydl.extract_info(f"https://www.youtube.com/watch?v={vid}", download=False)
            except Exception as exc:  # noqa: BLE001 — offline / removed video: keep old info
                print(f"[tubegrab]   offline metadata for {vid}: {exc}")
                info = dict(old)
            info = dict(info or {})
            # playlist context from the original grab
            top = _rel(path).split("/")[0] if "/" in _rel(path) else ""
            for k in ("playlist_id", "playlist_title", "playlist_index"):
                if old.get(k) is not None:
                    info[k] = old[k]
            if not info.get("playlist_index"):
                im = INDEX_PREFIX_RE.match(path.name)
                if im and info.get("playlist_id"):
                    info["playlist_index"] = int(im.group(1))
            if info.get("playlist_id") and old_folders.get(top, {}).get("url"):
                info["playlist_webpage_url"] = old_folders[top]["url"]
            plan = _plan(info, audio=kind == "audio", playlist_mode=bool(info.get("playlist_id")))
            rel = _unique(plan["rel"], path.suffix.lower(), vid)
            dest = DOWNLOAD_DIR / (rel + path.suffix.lower())
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(path), str(dest))
            _prune_empty_dirs(path.parent)
            _index_set(vid, kind, _rel(dest))
            _note_folder(Path(rel).parent.as_posix(), plan["collection_title"], plan["collection_url"])
            _write_meta(info, _GrabDL._meta_extra(plan))
            if plan["section"] == "Music":
                t = plan["tags"]
                _retag(dest, {"title": t.get("track"), "artist": t.get("artist"), "albumartist": t.get("album_artist"),
                              "album": t.get("album"), "tracknumber": t.get("track_number"),
                              "date": t.get("release_year")})
                cover = META_DIR / f"{vid}.jpg"
                if cover.exists():
                    _crop_square(cover)
                    _embed_cover(dest, cover)
            print(f"[tubegrab]   {path.name}  →  {_rel(dest)}")
    with _meta_lock:   # drop folder records of the old layout
        folders = {k: v for k, v in _folders().items() if (DOWNLOAD_DIR / k).is_dir()}
        _write_json(FOLDERS_FILE, folders)
    for f in META_DIR.glob("OLAK5uy_*"):   # old playlist cover images
        f.unlink(missing_ok=True)


def _merge_into(src: Path, dst: Path, folders: dict) -> None:
    """Move a folder's contents into another (or rename it), keeping the index and
    folder records in step. Name clashes stay behind for the user to look at."""
    old_key, new_key = _rel(src), _rel(dst)
    if not dst.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        src.rename(dst)
        _index_move(old_key, new_key)
    else:
        for f in list(src.iterdir()):
            if (dst / f.name).exists():
                if f.is_dir():
                    _merge_into(f, dst / f.name, folders)
                continue
            shutil.move(str(f), str(dst / f.name))
            _index_move(f"{old_key}/{f.name}", f"{new_key}/{f.name}")
        _prune_empty_dirs(src)
    for k in [k for k in folders if k == old_key or k.startswith(old_key + "/")]:
        entry = folders.pop(k)
        folders.setdefault(new_key + k[len(old_key):], entry)
    print(f"[tubegrab] merged {old_key} into {new_key}")


def _pick_artist(names: list[str], music: Path) -> str:
    """'Melvins' over 'MELVINS' (YouTube Music shouts some credits); else the bigger folder."""
    size = lambda n: sum(1 for _ in (music / n).rglob("*"))   # noqa: E731
    return sorted(names, key=lambda n: (n.isupper() and not all(x.isupper() for x in names), -size(n)))[0]


def _refile_playlist_audio() -> None:
    """Move songs an older version put in Playlists/ (because they came from a
    regular playlist link) into Music/Artist/Album, using what YouTube said."""
    root = DOWNLOAD_DIR / "Playlists"
    if not root.is_dir():
        return
    by_path = _ids_by_path()
    moved = 0
    albums: dict[str, dict | None] = {}   # playlist id → identified album
    covers: dict[str, dict] = {}
    for path in sorted(root.glob("*/*")):
        if not path.is_file() or path.suffix.lower() not in AUDIO_EXTS:
            continue
        vid = _video_id(path, by_path)
        meta = _meta_for(vid)
        if not vid or not meta.get("playlist_id") or meta.get("placed") == "user":
            continue
        album = None
        if not meta.get("album"):   # fan uploads: one album after all? (_identify_album)
            pl = meta["playlist_id"]
            if pl not in albums:
                albums[pl] = _album_of_folder(path.parent, pl, by_path)
            album = albums[pl]
            if not album:
                continue
        plan = _plan(dict(meta), audio=True, playlist_mode=True, album=album)
        if plan["section"] != "Music":
            continue
        ext = path.suffix.lower()
        rel = _unique(plan["rel"], ext, vid)
        dest = DOWNLOAD_DIR / (rel + ext)
        dest.parent.mkdir(parents=True, exist_ok=True)
        old = _rel(path)
        shutil.move(str(path), str(dest))
        _index_move(old, _rel(dest))
        _note_folder(Path(rel).parent.as_posix(), plan["collection_title"], plan["collection_url"])
        _write_meta({"id": vid}, _GrabDL._meta_extra(plan))
        t = plan["tags"]
        _retag(dest, {"title": t.get("track"), "artist": t.get("artist"), "albumartist": t.get("album_artist"),
                      "album": t.get("album"), "tracknumber": t.get("track_number"), "date": t.get("release_year")})
        cover = META_DIR / f"{vid}.jpg"
        if cover.exists():
            _crop_square(cover)
            _embed_cover(dest, cover)
        _prune_empty_dirs(path.parent)
        moved += 1
        if album:
            covers[Path(rel).parent.as_posix()] = album
    if moved:
        _forget_gone_folders()
        print(f"[tubegrab] moved {moved} song(s) from Playlists/ into Music/")
    for key, album in covers.items():
        _album_cover(key, album)


def _album_of_folder(folder: Path, pl_id: str, by_path: dict) -> dict | None:
    """_identify_album for a playlist folder already downloaded (titles from .meta)."""
    entries = []
    for p in folder.iterdir():
        if p.is_file() and p.suffix.lower() in AUDIO_EXTS:
            m = _meta_for(_video_id(p, by_path))
            if m.get("id") and m.get("playlist_id") == pl_id and not m.get("album"):
                entries.append(m)
    if not entries:
        return None
    entries.sort(key=lambda m: m.get("playlist_index") or 0)
    return _identify_album(pl_id, entries[0].get("playlist_title") or folder.name,
                           [{"id": m["id"], "title": m.get("title")} for m in entries])


def tidy_music() -> None:
    """Repair albums whose tracks YouTube described inconsistently:
    * artist folders that differ only in case ('MELVINS' + 'Melvins') merge;
    * 'Album' + 'Album (1993)' merge, and an album without a year gets the one
      its tracks agree on;
    * tags (album artist, year) are made consistent across the album;
    * 'Artist - … (Official Video)' noise is stripped from track names."""
    with _tidy_lock:
        _refile_playlist_audio()
    music = DOWNLOAD_DIR / "Music"
    if not music.is_dir():
        return
    with _tidy_lock, _meta_lock:
        folders = _folders()
        # 1. artists
        groups: dict[str, list[str]] = {}
        for d in music.iterdir():
            if d.is_dir() and not d.name.startswith("."):
                groups.setdefault(_fold(d.name), []).append(d.name)
        for names in groups.values():
            if len(names) > 1:
                keep = _pick_artist(names, music)
                for n in names:
                    if n != keep:
                        _merge_into(music / n, music / keep, folders)
        # 2. albums
        by_path = _ids_by_path()
        for artist_dir in [d for d in music.iterdir() if d.is_dir() and not d.name.startswith(".")]:
            albums: dict[str, list[Path]] = {}
            for d in artist_dir.iterdir():
                if d.is_dir() and d.name != SINGLES:
                    albums.setdefault(_fold(YEAR_RE.sub("", d.name)), []).append(d)
            for dirs in albums.values():
                dirs.sort(key=lambda d: (not YEAR_RE.search(d.name), -sum(1 for _ in d.iterdir())))
                target = dirs[0]
                for d in dirs[1:]:
                    _merge_into(d, target, folders)
                tracks = [p for p in target.iterdir() if p.is_file() and p.suffix.lower() in AUDIO_EXTS]
                metas = [(p, _video_id(p, by_path)) for p in tracks]
                year_m = YEAR_RE.search(target.name)
                year = year_m.group(1) if year_m else None
                if not year:
                    years = [str(_meta_for(v).get("release_year")) for _, v in metas if _meta_for(v).get("release_year")]
                    if years:
                        year = max(set(years), key=years.count)
                        dest = artist_dir / f"{target.name} ({year})"
                        if not dest.exists():
                            _merge_into(target, dest, folders)
                            target = dest
                # consistent tags: album artist = folder, one year, artist case fixed
                for p, vid in metas:
                    p = target / p.name
                    if not p.exists() or not vid:
                        continue
                    meta = _meta_for(vid)
                    want = {"album_artist": artist_dir.name, "release_year": year}
                    if meta.get("artist") and _fold(meta["artist"]) == _fold(artist_dir.name):
                        want["artist"] = artist_dir.name
                    if any(meta.get(k) != v for k, v in want.items() if v):
                        _write_meta({"id": vid}, want)
                        _retag(p, {"albumartist": artist_dir.name, "date": year, "artist": want.get("artist")})
        _write_json(FOLDERS_FILE, folders)

    by_path = _ids_by_path()
    for path in list(_iter_media()):
        rel = _rel(path)
        parts = rel.split("/")
        if parts[0] != "Music" or len(parts) != 4 or path.suffix.lower() not in AUDIO_EXTS:
            continue
        vid = _video_id(path, by_path)
        meta = _meta_for(vid)
        if not vid or meta.get("track") or not meta.get("title"):
            continue
        clean = _track_title(meta["title"], parts[1])
        prefix = INDEX_PREFIX_RE.match(path.name)
        stem = (prefix.group(0) if prefix else "") + _clean(clean)
        _write_meta({"id": vid}, {"track": clean})
        _retag(path, {"title": clean})
        if stem != path.stem:
            dest = path.with_name(stem + path.suffix)
            if not dest.exists():
                path.rename(dest)
                _index_move(rel, _rel(dest))
                print(f"[tubegrab] renamed {path.name} → {dest.name}")


def backfill_extras() -> None:
    """Fetch chapters + English captions for videos saved before TubeGrab kept them."""
    by_path = _ids_by_path()
    todo = []
    for path in _iter_media():
        vid = _video_id(path, by_path)
        if path.suffix.lower() in VIDEO_EXTS and vid and not _meta_for(vid).get("extras"):
            todo.append(vid)
    if not todo:
        return
    print(f"[tubegrab] fetching chapters/captions for {len(todo)} video(s)…")
    opts = {"quiet": True, "no_warnings": True, "skip_download": True, "js_runtimes": JS_RUNTIMES,
            "outtmpl": {"default": str(META_DIR / "%(id)s.%(ext)s"), "subtitle": str(META_DIR / "%(id)s.%(ext)s")},
            **SUB_OPTS}
    if COOKIES_FILE.exists():
        opts["cookiefile"] = str(COOKIES_FILE)
    with yt_dlp.YoutubeDL(opts) as ydl:
        for vid in dict.fromkeys(todo):
            try:
                info = ydl.extract_info(f"https://www.youtube.com/watch?v={vid}", download=True) or {}
            except Exception as exc:  # noqa: BLE001 — offline / removed: try again next start
                print(f"[tubegrab]   {vid}: {exc}")
                continue
            _write_meta({"id": vid, "chapters": info.get("chapters")}, {"extras": 1})


def _startup() -> None:
    migrate_legacy()
    tidy_music()
    try:
        backfill_extras()
    except Exception as exc:  # noqa: BLE001
        print(f"[tubegrab] backfill failed: {exc}")
    backfill_lyrics()
    _indexer.ready.wait()
    warm_thumbs()



# ---------------------------------------------------------------- API

@app.post("/api/download")
def api_download():
    data = request.get_json(force=True, silent=True) or {}
    url = (data.get("url") or "").strip()
    fmt = data.get("format") or DEFAULT_FORMAT
    fmt = FORMAT_ALIASES.get(fmt, fmt)
    playlist = bool(data.get("playlist", True))

    if not url:
        return jsonify(error="A URL is required."), 400
    if not url.lower().startswith(("http://", "https://")):
        return jsonify(error="URL must start with http:// or https://"), 400
    if fmt not in FORMATS:
        return jsonify(error="Unknown format."), 400

    job_id = uuid.uuid4().hex[:12]
    job = {
        "id": job_id, "url": url, "format": fmt, "playlist": playlist,
        "status": "queued", "title": None, "error": None,
        "percent": None, "speed": None, "eta": None,
        "downloaded": 0, "total": None, "current_file": None,
        "playlist_index": None, "playlist_count": None, "entry_count": None,
        "files": [], "created_at": _now(), "finished_at": None,
        "cancel": False,
    }
    with _lock:
        _jobs[job_id] = job
    _jobs_dirty.set()
    threading.Thread(target=_run_job, args=(job_id, url, fmt, playlist), daemon=True).start()
    return jsonify(job=serialize(job))


@app.get("/api/jobs")
def api_jobs():
    with _lock:
        jobs = [serialize(j) for j in _jobs.values()]
    jobs.sort(key=lambda j: j["created_at"], reverse=True)
    return jsonify(jobs=jobs)


@app.post("/api/jobs/<job_id>/cancel")
def api_cancel(job_id: str):
    with _lock:
        job = _jobs.get(job_id)
        if not job:
            return jsonify(error="Unknown job."), 404
        if job["status"] in ("queued", "downloading"):
            job["cancel"] = True
    return jsonify(ok=True)


@app.post("/api/files/delete")
def api_files_delete():
    data = request.get_json(force=True, silent=True) or {}
    target = _safe_path(data.get("name") or "")
    if target is None:
        return jsonify(error="File not found."), 404
    _delete_media(target)
    _playlists_drop_missing()
    _forget_gone_folders()
    return jsonify(ok=True)


def _delete_media(target: Path) -> None:
    """Delete one library file plus its index entry (and metadata once no copy is left)."""
    rel = _rel(target)
    vid = _video_id(target)
    target.unlink()
    _prune_empty_dirs(target.parent)
    _index_drop(rel)
    if vid and not any(k.startswith(f"{vid}:") for k in _index()):
        for p in META_DIR.glob(f"{vid}.*"):
            p.unlink(missing_ok=True)


def _forget_gone_folders() -> None:
    """Drop folder records (and uploaded covers) of folders that no longer exist."""
    with _meta_lock:
        folders = _folders()
        gone = [k for k in folders if not (DOWNLOAD_DIR / k).is_dir()]
        for k in gone:
            if folders[k].get("cover_file"):
                (COVERS_DIR / folders[k]["cover_file"]).unlink(missing_ok=True)
            folders.pop(k)
        if gone:
            _write_json(FOLDERS_FILE, folders)


@app.post("/api/collections/delete")
def api_collection_delete():
    """Delete an album / channel / downloaded playlist folder and every file in it.
    (Your own playlists are different: deleting one never touches files.)"""
    data = request.get_json(force=True, silent=True) or {}
    folder = _safe_path(data.get("key") or "", dir_ok=True)
    if folder is None or not folder.is_dir():
        return jsonify(error="Collection not found."), 404
    parts = _rel(folder).split("/")
    if folder == DOWNLOAD_DIR or len(parts) < (3 if parts[0] == "Music" else 2):   # an album, never a whole artist
        return jsonify(error="That folder can't be deleted here."), 400
    with _lock:
        if any(j.get("status") in ("queued", "downloading") for j in _jobs.values()):
            return jsonify(error="Wait for downloads to finish first."), 409
    count = 0
    for p in sorted(folder.rglob("*"), reverse=True):
        if p.is_file() and p.suffix.lower() in MEDIA_EXTS:
            _delete_media(p)
            count += 1
    if folder.exists():   # leftovers (partial downloads, stray files) go too
        shutil.rmtree(folder, ignore_errors=True)
        _prune_empty_dirs(folder.parent)
    _playlists_drop_missing()
    _forget_gone_folders()
    return jsonify(ok=True, deleted=count)


# ---------------------------------------------------------------- transcripts
# YouTube captions (saved as .meta/<id>.<lang>.vtt) become clean timed lines for
# the player's CC, the transcript panel and library-wide search.

_TS_RE = re.compile(r"(?:(\d+):)?(\d{2}):(\d{2})[.,](\d{3})")
_transcripts: dict[str, tuple[float, list]] = {}   # vtt path → (mtime, segments)


def _ts(v: str) -> float:
    m = _TS_RE.match(v.strip())
    if not m:
        return 0.0
    h, mi, se, ms = m.groups()
    return int(h or 0) * 3600 + int(mi) * 60 + int(se) + int(ms) / 1000


_CUE_SPLIT = re.compile(r"\n\s*\n")
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def _parse_vtt(text: str) -> list[dict]:
    """Cues → [{start, end, text}], collapsing YouTube's rolling auto-captions
    (each cue repeats the previous line) into one entry per spoken line."""
    import html
    segs: list[dict] = []
    last: list[str] = []
    for block in _CUE_SPLIT.split(text.replace("\r", "")):
        lines = block.strip().split("\n")
        i = next((k for k, l in enumerate(lines) if "-->" in l), None)
        if i is None:
            continue
        a, b = lines[i].split("-->")[:2]
        start, end = _ts(a), _ts(b.split()[0] if b.split() else b)
        if end - start < 0.05:          # zero-length "transition" cues
            continue
        body = []
        for l in lines[i + 1:]:
            if "<" in l:
                l = _TAG.sub("", l)
            if "&" in l:
                l = html.unescape(l)
            l = _WS.sub(" ", l).strip()
            if l:
                body.append(l)
        new = [l for l in body if l not in last]
        if new:
            segs.append({"start": round(start, 2), "end": round(end, 2), "text": " ".join(new)})
        last = body or last
    for k in range(len(segs) - 1):      # a line lasts until the next one starts (max 8 s)
        segs[k]["end"] = round(min(segs[k + 1]["start"], segs[k]["start"] + 8), 2)
    return segs


def _sub_file(vid: str | None) -> Path | None:
    if not vid:
        return None
    for lang in SUB_LANGS:
        p = META_DIR / f"{vid}.{lang}.vtt"
        if p.exists() and p.stat().st_size > 0:
            return p
    return None


def _transcript(vid: str | None) -> list[dict]:
    path = _sub_file(vid)
    if not path:
        return []
    mtime = path.stat().st_mtime
    cached = _transcripts.get(str(path))
    if cached and cached[0] == mtime:
        return cached[1]
    try:
        segs = _parse_vtt(path.read_text("utf-8", errors="replace"))
    except Exception:  # noqa: BLE001
        segs = []
    _transcripts[str(path)] = (mtime, segs)
    while len(_transcripts) > 16:   # only the few being watched; search uses the index
        _transcripts.pop(next(iter(_transcripts)))
    return segs


@app.get("/api/transcript/<path:name>")
def api_transcript(name: str):
    path = _safe_path(name)
    if path is None:
        return jsonify(error="File not found."), 404
    return jsonify(segments=_transcript(_video_id(path)))


@app.get("/api/search")
def api_search():
    """Find words spoken in your videos (full-text index of their captions)."""
    q = re.sub(r"\s+", " ", request.args.get("q") or "").strip()
    terms = re.findall(r"\w+", q)
    if len(q) < 3 or not terms:
        return jsonify(results=[])
    match = '"' + " ".join(terms) + '"' + ("" if q.endswith(" ") else "*")
    con = _db()
    try:
        # one pass over the matches (capped, so a word said everywhere stays quick)
        rows = con.execute("SELECT name, start, text, i FROM captions WHERE captions MATCH ? LIMIT 20000",
                           (match,)).fetchall()
    except sqlite3.OperationalError:
        return jsonify(results=[])
    per: dict[str, list] = {}
    for r in rows:
        per.setdefault(r["name"], []).append(r)
    top = sorted(per, key=lambda n: -len(per[n]))[:30]
    files = _rows_by_names(con, top)
    results = []
    for name in top:
        if name not in files:
            continue
        hits, last = [], -10
        for h in sorted(per[name], key=lambda r: r["i"]):
            if h["i"] > last + 1:   # the next line repeats this one's tail
                hits.append({"t": h["start"], "text": h["text"]})
                if len(hits) == 3:
                    break
            last = h["i"]
        results.append({"name": name, "count": len(per[name]), "hits": hits, "file": files[name]})
    return jsonify(results=results)


# ---------------------------------------------------------------- lyrics
# Looked up on LRCLIB (free, open lyrics database; only artist/title/album/length
# are sent). Synced lyrics → <id>.lrc, plain only → <id>.lyrics.txt, nothing
# found → <id>.nolyrics (so we don't ask again until you retry).

LRCLIB = "https://lrclib.net/api"
_LRC_LINE = re.compile(r"\[(\d+):(\d{2}(?:[.:]\d{1,3})?)\]")
_lyrics_lock = threading.Lock()
_lrclib_last = 0.0


def _lrclib(path: str, params: dict):
    import urllib.error
    import urllib.parse
    import urllib.request
    import time
    global _lrclib_last
    url = f"{LRCLIB}/{path}?{urllib.parse.urlencode({k: v for k, v in params.items() if v})}"
    req = urllib.request.Request(url, headers={"User-Agent": "TubeGrab (self-hosted music library)"})
    for attempt in range(4):
        wait = _lrclib_last + 1.5 - time.monotonic()   # ≤ 1 request / 1.5 s
        if wait > 0:
            time.sleep(wait)
        _lrclib_last = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code == 429 and attempt < 3:   # slow down as asked
                time.sleep(float(e.headers.get("Retry-After") or 30))
                continue
            raise


def _parse_lrc(text: str) -> list[dict]:
    """'[01:02.50] line' → [{start, end, text}] (empty lines end the previous one)."""
    rows = []
    for line in text.splitlines():
        stamps = _LRC_LINE.findall(line)
        if not stamps:
            continue
        words = _LRC_LINE.sub("", line).strip()
        for mi, se in stamps:
            rows.append((int(mi) * 60 + float(se.replace(":", ".")), words))
    rows.sort(key=lambda r: r[0])
    segs = []
    for k, (t, words) in enumerate(rows):
        end = rows[k + 1][0] if k + 1 < len(rows) else t + 6
        if words:
            segs.append({"start": round(t, 2), "end": round(end, 2), "text": words})
    return segs


def _embed_lyrics(path: Path, text: str) -> None:
    if path.suffix.lower() != ".m4a" or not text:
        return
    try:
        from mutagen.mp4 import MP4
        restore = _keep_mtime(path)
        f = MP4(str(path))
        f["\xa9lyr"] = [text]
        f.save()
        restore()
    except Exception as exc:  # noqa: BLE001
        print(f"[tubegrab] lyrics embed failed for {path.name}: {exc}")


def fetch_lyrics(path: Path, force: bool = False) -> str | None:
    """Find lyrics for a library song. Returns "synced", "plain" or None."""
    vid = _video_id(path)
    if not vid or path.suffix.lower() not in AUDIO_EXTS:
        return None
    lrc, txt, miss = (META_DIR / f"{vid}{ext}" for ext in (".lrc", ".lyrics.txt", ".nolyrics"))
    if not force and (lrc.exists() or txt.exists() or miss.exists()):
        return "synced" if lrc.exists() else "plain" if txt.exists() else None
    meta = _meta_for(vid)
    artist = meta.get("album_artist") or meta.get("artist") or _clean_channel(meta.get("uploader"))
    track = meta.get("track") or meta.get("title")
    dur = meta.get("duration") or _probe(path)["duration"]
    if not artist or not track:
        return None
    with _lyrics_lock:   # be gentle with the free service: one lookup at a time
        rec = _lrclib("get", {"artist_name": artist, "track_name": track,
                              "album_name": meta.get("album"), "duration": round(dur) if dur else None})
        if not rec:   # no exact match: search, prefer the closest length
            found = _lrclib("search", {"artist_name": artist, "track_name": track}) or []
            found = [r for r in found if r.get("plainLyrics") or r.get("syncedLyrics")]
            if dur:
                found.sort(key=lambda r: abs((r.get("duration") or 0) - dur))
            rec = found[0] if found else None
    for p in (lrc, txt, miss):
        p.unlink(missing_ok=True)
    if not rec or rec.get("instrumental") or not (rec.get("plainLyrics") or rec.get("syncedLyrics")):
        miss.write_text(_now())
        _meta_changed(walk=False, vids=[vid])
        return None
    # synced lines only line up when it's the same recording (music videos often add intros)
    same_cut = not dur or abs((rec.get("duration") or 0) - dur) <= 3
    plain = rec.get("plainLyrics") or "\n".join(sg["text"] for sg in _parse_lrc(rec.get("syncedLyrics") or ""))
    if rec.get("syncedLyrics") and same_cut:
        lrc.write_text(rec["syncedLyrics"], "utf-8")
        kind = "synced"
    else:
        txt.write_text(plain, "utf-8")
        kind = "plain"
    _embed_lyrics(path, plain)
    _meta_changed(walk=False, vids=[vid])
    return kind


def _fetch_lyrics_bg(path: Path) -> None:
    def run():
        try:
            fetch_lyrics(path)
        except Exception as exc:  # noqa: BLE001 — offline etc.; retried on next start
            print(f"[tubegrab] lyrics lookup failed for {path.name}: {exc}")
    threading.Thread(target=run, daemon=True, name="lyrics").start()


def backfill_lyrics() -> None:
    """Look up lyrics for songs that haven't been checked yet."""
    by_path = _ids_by_path()
    for path in list(_iter_media()):
        if _rel(path).split("/")[0] != "Music" or path.suffix.lower() not in AUDIO_EXTS:
            continue
        vid = _video_id(path, by_path)
        if not vid or any((META_DIR / f"{vid}{e}").exists() for e in (".lrc", ".lyrics.txt", ".nolyrics")):
            continue
        try:
            fetch_lyrics(path)
        except Exception as exc:  # noqa: BLE001 — offline: stop, try again next start
            print(f"[tubegrab] lyrics backfill paused: {exc}")
            return


@app.get("/api/lyrics/<path:name>")
def api_lyrics(name: str):
    path = _safe_path(name)
    if path is None:
        return jsonify(error="File not found."), 404
    vid = _video_id(path)
    lrc, txt = META_DIR / f"{vid}.lrc", META_DIR / f"{vid}.lyrics.txt"
    if vid and lrc.exists():
        return jsonify(synced=_parse_lrc(lrc.read_text("utf-8", errors="replace")), plain=None)
    if vid and txt.exists():
        return jsonify(synced=None, plain=txt.read_text("utf-8", errors="replace"))
    return jsonify(synced=None, plain=None)


@app.post("/api/lyrics/<path:name>")
def api_lyrics_refresh(name: str):
    """Look again (e.g. after fixing a song's title)."""
    path = _safe_path(name)
    if path is None:
        return jsonify(error="File not found."), 404
    try:
        kind = fetch_lyrics(path, force=True)
    except Exception:  # noqa: BLE001
        return jsonify(error="Couldn't reach the lyrics service."), 502
    return jsonify(found=kind)


# ---------------------------------------------------------------- progress
# Where you stopped in each video (and whether you finished it), shared by
# every device that opens the library.

def _progress() -> dict:
    return _read_json(PROGRESS_FILE, {})


@app.get("/api/progress")
def api_progress_get():
    """Resume positions + watched flags, by library name (only items that have one)."""
    rows = _db().execute("SELECT f.name, p.pos, p.watched FROM progress p JOIN files f ON f.ref=p.ref "
                         "WHERE p.pos>0 OR p.watched=1").fetchall()
    return _cached_json(_json_bytes({r[0]: {"pos": r[1], "watched": bool(r[2])} for r in rows}))


@app.post("/api/progress")
def api_progress():
    data = request.get_json(force=True, silent=True) or {}   # also sent with sendBeacon (text/plain)
    path = _safe_path(data.get("name") or "")
    if path is None:
        return jsonify(error="File not found."), 404
    ref = _pl_ref(path)
    with _meta_lock:
        prog = _progress()
        entry = prog.get(ref, {})
        if "pos" in data:
            try:
                entry["pos"] = max(0.0, round(float(data["pos"]), 1))
            except (TypeError, ValueError):
                return jsonify(error="Bad position."), 400
        if "watched" in data:
            entry["watched"] = bool(data["watched"])
            if entry["watched"]:
                entry["pos"] = 0.0
        entry["at"] = _now()
        prog[ref] = entry
        _write_json(PROGRESS_FILE, prog)
    con = _db()
    old = con.execute("SELECT watched FROM progress WHERE ref=?", (ref,)).fetchone()
    con.execute("INSERT OR REPLACE INTO progress VALUES(?,?,?)",
                (ref, float(entry.get("pos") or 0), 1 if entry.get("watched") else 0))
    _publish("progress", {"name": _rel(path), "pos": entry.get("pos") or 0, "watched": bool(entry.get("watched"))})
    if bool(old and old[0]) != bool(entry.get("watched")):
        _lib_bump()   # watched counts on collection cards
    return jsonify(ok=True)


# ---------------------------------------------------------------- playlists
# Your own playlists: ordered references to library items ("<id>:<kind>", which
# survive renames and re-filing, or "path:<rel>" for files without an id). The
# files stay where they are, so one track can be in any number of playlists.

def _pl_all() -> dict:
    return _read_json(PLAYLISTS_FILE, {})


def _pl_ref(path: Path, by_path: dict | None = None) -> str:
    vid = _video_id(path, by_path)
    return f"{vid}:{_kind(path)}" if vid else f"path:{_rel(path)}"


def _pl_resolve(ref: str, idx: dict) -> str | None:
    rel = ref[5:] if ref.startswith("path:") else idx.get(ref)
    return rel if rel and (DOWNLOAD_DIR / rel).is_file() else None


def _tile(f: dict) -> dict:
    return {"name": f["name"], "tv": f["tv"], "color": f["color"], "kind": f["kind"]}


def _pl_out(pid: str, p: dict, idx: dict, full: bool = False) -> dict:
    names = [r for r in (_pl_resolve(ref, idx) for ref in p.get("items", [])) if r]
    rows = _rows_by_names(_db(), names)
    files = [rows[n] for n in names if n in rows]
    cover = p.get("cover") or {}
    cover_item = _pl_resolve(cover["item"], idx) if cover.get("item") else None
    seen: set = set()
    mosaic = []   # one tile per album (its tracks share the art), every video its own
    for f in files:
        k = f["collection"] if f["section"] == "Music" and f["collection"] else f["name"]
        if k not in seen:
            seen.add(k)
            mosaic.append(_tile(f))
            if len(mosaic) == 4:
                break
    out = {
        "id": pid, "title": p.get("title") or "Untitled", "description": p.get("description") or "",
        "items": [f["name"] for f in files], "created": p.get("created"), "updated": p.get("updated"),
        "count": len(files), "duration": sum(f["duration"] or 0 for f in files),
        "cover_item": cover_item, "cover_tile": _tile(rows[cover_item]) if cover_item in rows else None,
        "cover_upload": cover.get("upload"),   # version string → /playlist-cover/<id>?v=
        "mosaic": mosaic,
    }
    if full:
        out["files"] = files
    return out


def _playlists_out() -> list[dict]:
    idx = _index()
    out = [_pl_out(pid, p, idx) for pid, p in _pl_all().items()]
    return sorted(out, key=lambda p: p["updated"] or "", reverse=True)


@app.get("/api/playlists")
def api_playlists():
    return _cached_json(_json_bytes({"playlists": _playlists_out()}))


@app.get("/api/playlists/<pid>")
def api_playlist(pid: str):
    p = _pl_all().get(pid)
    if p is None:
        return jsonify(error="Playlist not found."), 404
    return _cached_json(_json_bytes({"playlist": _pl_out(pid, p, _index(), full=True)}))


def _playlists_drop_missing() -> None:
    """Forget references to files that no longer exist anywhere."""
    with _meta_lock:
        pls, idx = _pl_all(), _index()
        changed = False
        for p in pls.values():
            keep = [r for r in p.get("items", []) if _pl_resolve(r, idx)]
            if keep != p.get("items", []):
                p["items"], changed = keep, True
            if (p.get("cover") or {}).get("item") and not _pl_resolve(p["cover"]["item"], idx):
                p["cover"], changed = None, True
        if changed:
            _write_json(PLAYLISTS_FILE, pls)


def _pl_refs(names) -> list[str] | None:
    """Library names from the client → unique references (None if malformed)."""
    if not isinstance(names, list):
        return None
    by_path = _ids_by_path()
    refs: list[str] = []
    for n in names:
        path = _safe_path(n) if isinstance(n, str) else None
        if path is None:
            return None
        ref = _pl_ref(path, by_path)
        if ref not in refs:
            refs.append(ref)
    return refs


def _pl_text(data: dict, p: dict) -> str | None:
    """Apply title/description; returns an error message or None."""
    if "title" in data:
        title = re.sub(r"\s+", " ", str(data.get("title") or "")).strip()
        if not title:
            return "Give the playlist a name."
        p["title"] = title[:120]
    if "description" in data:
        p["description"] = str(data.get("description") or "").strip()[:1000]
    return None


@app.post("/api/playlists")
def api_playlist_create():
    data = request.get_json(force=True, silent=True) or {}
    p: dict = {"items": [], "cover": None, "created": _now()}
    err = _pl_text({"title": data.get("title"), "description": data.get("description")}, p)
    if err:
        return jsonify(error=err), 400
    refs = _pl_refs(data.get("items") or [])
    if refs is None:
        return jsonify(error="Some items weren't found."), 400
    p["items"], p["updated"] = refs, _now()
    pid = uuid.uuid4().hex[:10]
    with _meta_lock:
        pls = _pl_all()
        pls[pid] = p
        _write_json(PLAYLISTS_FILE, pls)
    return jsonify(playlist=_pl_out(pid, p, _index()))


@app.patch("/api/playlists/<pid>")
def api_playlist_update(pid: str):
    """Any of: title, description, items (full ordered list of names),
    cover_item (a name, or null to go back to the automatic cover)."""
    data = request.get_json(force=True, silent=True) or {}
    with _meta_lock:
        pls = _pl_all()
        p = pls.get(pid)
        if p is None:
            return jsonify(error="Playlist not found."), 404
        err = _pl_text(data, p)
        if err:
            return jsonify(error=err), 400
        if "items" in data:
            refs = _pl_refs(data["items"])
            if refs is None:
                return jsonify(error="Some items weren't found."), 400
            p["items"] = refs
        if "cover_item" in data:
            name = data["cover_item"]
            if name is None:
                p["cover"] = None
            else:
                refs = _pl_refs([name])
                if not refs:
                    return jsonify(error="That item wasn't found."), 400
                p["cover"] = {"item": refs[0]}
            (PL_COVERS_DIR / f"{pid}.jpg").unlink(missing_ok=True)
        p["updated"] = _now()
        _write_json(PLAYLISTS_FILE, pls)
    return jsonify(playlist=_pl_out(pid, p, _index()))


@app.delete("/api/playlists/<pid>")
def api_playlist_delete(pid: str):
    """Deletes the playlist only — never the files in it."""
    with _meta_lock:
        pls = _pl_all()
        if pls.pop(pid, None) is None:
            return jsonify(error="Playlist not found."), 404
        _write_json(PLAYLISTS_FILE, pls)
    (PL_COVERS_DIR / f"{pid}.jpg").unlink(missing_ok=True)
    return jsonify(ok=True)


def _save_picture(up, dest: Path, wide: bool = False) -> bool:
    """Uploaded image → cropped JPEG at `dest` (square, or 16:9 when `wide`)."""
    crop = ("crop='min(iw,ih*16/9)':'min(ih,iw*9/16)',scale='min(1600,iw)':-2" if wide
            else "crop='min(iw,ih)':'min(iw,ih)',scale='min(1000,iw)':-2")
    tmp = dest.with_name(f".{uuid.uuid4().hex}.upload")
    out = dest.with_name(f".{uuid.uuid4().hex}.jpg")
    try:
        up.save(tmp)
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(tmp), "-frames:v", "1",
                        "-vf", crop, "-q:v", "3", "-y", str(out)], timeout=30, check=True)
        if not out.exists() or out.stat().st_size == 0:
            return False
        out.replace(dest)
        return True
    except Exception:  # noqa: BLE001
        return False
    finally:
        tmp.unlink(missing_ok=True)
        out.unlink(missing_ok=True)


BAD_PICTURE = "That file couldn't be read as an image (try a JPEG or PNG)."


@app.post("/api/playlists/<pid>/cover")
def api_playlist_upload_cover(pid: str):
    """Upload a picture: cropped square and stored as a JPEG."""
    if pid not in _pl_all():
        return jsonify(error="Playlist not found."), 404
    up = request.files.get("file")
    if up is None or not up.filename:
        return jsonify(error="Choose an image file."), 400
    if not _save_picture(up, PL_COVERS_DIR / f"{pid}.jpg"):
        return jsonify(error=BAD_PICTURE), 400
    with _meta_lock:
        pls = _pl_all()
        if pid in pls:
            pls[pid]["cover"] = {"upload": uuid.uuid4().hex[:8]}
            pls[pid]["updated"] = _now()
            _write_json(PLAYLISTS_FILE, pls)
    return jsonify(playlist=_pl_out(pid, pls[pid], _index()))


@app.get("/playlist-cover/<pid>")
def playlist_cover(pid: str):
    if not re.fullmatch(r"[0-9a-f]{10}", pid):
        return jsonify(error="Not found."), 404
    path = PL_COVERS_DIR / f"{pid}.jpg"
    if not path.exists():
        return jsonify(error="Not found."), 404
    return _send_image(path, 31536000 if request.args.get("v") else 86400, "image/jpeg")


@app.post("/api/collections/cover")
def api_collection_cover():
    """Use one item's thumbnail as the collection's cover; for albums it is also
    shown for, and embedded into, every track."""
    data = request.get_json(force=True, silent=True) or {}
    folder = _safe_path(data.get("key") or "", dir_ok=True)
    item = _safe_path(data.get("name") or "")
    if folder is None or item is None or item.parent != folder:
        return jsonify(error="That item isn't in this collection."), 404
    vid = _video_id(item)
    art = _meta_thumb(vid)
    if not art:
        return jsonify(error="That item has no thumbnail to use."), 400
    key = _rel(folder)
    with _meta_lock:
        folders = _folders()
        entry = folders.setdefault(key, {})
        entry["cover"] = vid
        old = entry.pop("cover_file", None)
        _write_json(FOLDERS_FILE, folders)
    if old:
        (COVERS_DIR / old).unlink(missing_ok=True)
    if art.suffix.lower() == ".jpg":
        for p in folder.iterdir():
            if p.is_file() and p.suffix.lower() in AUDIO_EXTS:
                _embed_cover(p, art)
    return jsonify(ok=True)


@app.post("/api/collections/cover/upload")
def api_collection_cover_upload():
    """Use your own picture as a collection's cover (albums: square, also shown
    for and embedded into every track; channels/playlists: 16:9)."""
    folder = _safe_path(request.form.get("key") or "", dir_ok=True)
    if folder is None or folder == DOWNLOAD_DIR:
        return jsonify(error="Collection not found."), 404
    up = request.files.get("file")
    if up is None or not up.filename:
        return jsonify(error="Choose an image file."), 400
    key = _rel(folder)
    name = f"{uuid.uuid4().hex[:10]}.jpg"
    if not _save_picture(up, COVERS_DIR / name, wide=_collection_info(key)["section"] != "Music"):
        return jsonify(error=BAD_PICTURE), 400
    with _meta_lock:
        folders = _folders()
        entry = folders.setdefault(key, {})
        old = entry.get("cover_file")
        entry["cover_file"] = name
        _write_json(FOLDERS_FILE, folders)
    if old:
        (COVERS_DIR / old).unlink(missing_ok=True)
    for p in folder.iterdir():
        if p.is_file() and p.suffix.lower() in AUDIO_EXTS:
            _embed_cover(p, COVERS_DIR / name)
    return jsonify(ok=True)


@app.get("/collection-cover/<name>")
def collection_cover(name: str):
    if not re.fullmatch(r"[0-9a-f]{10}\.jpg", name) or not (COVERS_DIR / name).is_file():
        return jsonify(error="Not found."), 404
    return _send_image(COVERS_DIR / name, 31536000, "image/jpeg")   # names are unique per upload


@app.post("/api/collections/edit")
def api_collection_edit():
    """Fix a collection YouTube got wrong: rename/move its folder (and, for
    music, rewrite artist/album/year tags)."""
    data = request.get_json(force=True, silent=True) or {}
    src = _safe_path(data.get("key") or "", dir_ok=True)
    if src is None:
        return jsonify(error="Collection not found."), 404
    key = _rel(src)
    c = _collection_info(key)
    title = (data.get("title") or "").strip()
    artist = (data.get("artist") or "").strip()
    year = str(data.get("year") or "").strip()
    if year and not re.fullmatch(r"\d{4}", year):
        return jsonify(error="Year must be four digits."), 400

    if c["section"] == "Music":
        artist = artist or c["artist"]
        if c["kind"] == "singles":
            folder, title = SINGLES, None
        else:
            title = title or c["title"]
            folder = f"{_clean(title)} ({year})" if year else _clean(title)
        new_key = f"Music/{_clean(artist)}/{folder}"
    else:
        if not title:
            return jsonify(error="A name is required."), 400
        new_key = f"{'/'.join(key.split('/')[:-1])}/{_clean(title)}"

    dest = DOWNLOAD_DIR / new_key
    with _meta_lock:
        if new_key != key:
            if dest.exists():
                # merge into an existing folder (e.g. fixing a misspelt artist)
                for p in list(src.iterdir()):
                    if (dest / p.name).exists():
                        return jsonify(error=f"“{new_key}/{p.name}” already exists."), 409
                for p in list(src.iterdir()):
                    shutil.move(str(p), str(dest / p.name))
                _prune_empty_dirs(src)
            else:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(dest))
                _prune_empty_dirs(src.parent)
            _index_move(key, new_key)
        folders = _folders()
        entry = folders.pop(key, {})
        if title:
            entry["title"] = title
        if entry:
            folders[new_key] = entry
        _write_json(FOLDERS_FILE, folders)

    if c["section"] == "Music":
        tags = {"artist": artist, "albumartist": artist, "album": title, "date": year or None}
        for p in dest.iterdir():
            if p.is_file() and p.suffix.lower() in AUDIO_EXTS:
                _retag(p, tags)
                vid = _video_id(p)
                if vid:
                    _write_meta({"id": vid}, {"artist": artist, "album_artist": artist,
                                              "album": title, "release_year": year or None})
    return jsonify(ok=True, key=new_key)


# ---------------------------------------------------------------- Music ⇄ Videos
# Audio downloads land in Music; the odd lecture grabbed as audio can be moved
# to the video side (and a video, or a whole collection, the other way). Files
# you moved are marked "placed" in their metadata so tidying never moves them back.

def _move_file(path: Path, rel_noext: str, extra: dict, tags: dict | None = None) -> str:
    """Move one library file to `rel_noext` (+ its extension); returns its new name."""
    vid = _video_id(path)
    rel = _unique(rel_noext, path.suffix.lower(), vid)
    dest = DOWNLOAD_DIR / (rel + path.suffix)
    if dest != path:
        old = _rel(path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(dest))
        _prune_empty_dirs(path.parent)
        _index_move(old, _rel(dest))
    if vid:
        _write_meta({"id": vid}, {**extra, "placed": "user"})
    if tags:
        _retag(dest, tags)
    return _rel(dest)


def _channel_of(meta: dict) -> str:
    return (_clean_channel(meta.get("uploader") or meta.get("channel"))
            or meta.get("album_artist") or meta.get("artist") or "Unknown")


def _to_videos(path: Path, folder: str | None = None, idx: int | None = None) -> str:
    """Videos/<Channel>/<Title>, or into the playlist folder `folder` at position `idx`."""
    meta = _meta_for(_video_id(path))
    title = _clean(meta.get("title") or meta.get("track") or _display_title(path.name))
    if folder:
        return _move_file(path, f"{folder}/{int(idx):03d} - {title}" if idx else f"{folder}/{title}", {"section": "Playlists"})
    return _move_file(path, f"Videos/{_clean(_channel_of(meta))}/{title}", {"section": "Videos"})


def _to_music_single(path: Path) -> str:
    meta = _meta_for(_video_id(path))
    artist = meta.get("album_artist") or meta.get("artist") or _channel_of(meta)
    adir = _artist_dir(artist)
    title = meta.get("track") or _track_title(meta.get("title") or _display_title(path.name), artist)
    if meta.get("album") and meta.get("track_number"):   # was on an album: back into its slot
        adir, folder, year = _album_folder(artist, meta["album"], meta.get("release_year"), None)
        no = int(meta["track_number"])
        name = _move_file(path, f"Music/{adir}/{folder}/{no:02d} - {_clean(title)}",
                          {"section": "Music", "album_artist": adir, "track": title, "release_year": year},
                          {"title": title, "artist": artist, "albumartist": adir, "album": meta["album"],
                           "tracknumber": no, "date": year})
    else:
        name = _move_file(path, f"Music/{adir}/{SINGLES}/{_clean(title)}",
                          {"section": "Music", "album_artist": adir, "track": title},
                          {"title": title, "artist": artist, "albumartist": adir})
    if path.suffix.lower() in AUDIO_EXTS:
        _fetch_lyrics_bg(DOWNLOAD_DIR / name)
    return name


def _media_in(folder: Path) -> list[Path]:
    return sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in MEDIA_EXTS)


def _position(path: Path) -> int | None:
    m = INDEX_PREFIX_RE.match(path.name)
    return int(m.group(1)) if m else _meta_for(_video_id(path)).get("track_number")


def _playlist_to_album(src: Path, key: str) -> str:
    """A downloaded playlist (or folder) → Music/Artist/Album: the real album when
    MusicBrainz recognises it (_identify_album), else named after the playlist."""
    files = sorted(_media_in(src), key=lambda p: (_position(p) or 10**6, p.name))
    metas = [(p, _meta_for(_video_id(p))) for p in files]
    entries = [{"id": m["id"], "title": m.get("title")} for _, m in metas if m.get("id")]
    pl_ids = {m.get("playlist_id") for _, m in metas if m.get("playlist_id")}
    title = _folders().get(key, {}).get("title") or src.name
    album = None
    if len(pl_ids) == 1:
        pl = pl_ids.pop()
        with _albums_lock:   # you said it's music: ask again even if it didn't look like an album
            cache = _read_json(ALBUMS_FILE, {})
            if cache.get(pl, {}).get("none"):
                cache.pop(pl)
                _write_json(ALBUMS_FILE, cache)
        album = _identify_album(pl, title, entries)
    if not album:
        g = _guess_album(title, entries) or {}
        counts: dict[str, int] = {}
        for _, m in metas:
            counts[_channel_of(m)] = counts.get(_channel_of(m), 0) + 1
        artist = g.get("artist") or max(counts, key=counts.get, default="Unknown")
        album = {"artist": artist, "album": g.get("album") or title, "year": g.get("year"), "tracks": {},
                 "guess_artist": artist, "guess_album": g.get("album") or title}
    adir, folder, year = _album_folder(album["artist"], album["album"], album.get("year"), None)
    new_key = f"Music/{adir}/{folder}"
    for n, (p, m) in enumerate(metas, 1):
        t = album["tracks"].get(m.get("id")) or {
            "no": None if album.get("mbid") else (_position(p) or n),
            "title": m.get("track") or _local_title(m.get("title") or _display_title(p.name),
                                                     album.get("guess_artist"), album.get("guess_album"))}
        no = t.get("no")
        name = _move_file(p, f"{new_key}/{int(no):02d} - {_clean(t['title'])}" if no else f"{new_key}/{_clean(t['title'])}",
                          {"section": "Music", "album_artist": adir, "album": album["album"],
                           "track_number": no, "track": t["title"], "release_year": year},
                          {"title": t["title"], "artist": adir, "albumartist": adir, "album": album["album"],
                           "tracknumber": no, "date": year})
        if p.suffix.lower() in AUDIO_EXTS:
            _fetch_lyrics_bg(DOWNLOAD_DIR / name)
    _carry_folder(key, new_key, album["album"])
    if album.get("mbid"):
        _album_cover(new_key, album)
    return new_key


def _carry_folder(old: str, new: str, title: str | None) -> None:
    """A collection moved whole keeps its record (link, chosen cover) under the new folder."""
    with _meta_lock:
        folders = _folders()
        entry = folders.pop(old, None) or {}
        if entry.get("cover") or entry.get("cover_file") or entry.get("url"):
            target = folders.setdefault(new, {})
            for k in ("url", "cover", "cover_file"):
                if entry.get(k) and not target.get(k):
                    target[k] = entry[k]
            if title:
                target.setdefault("title", title)
        _write_json(FOLDERS_FILE, folders)


@app.post("/api/move")
def api_move():
    """Move a file (`name`) or a whole collection (`key`) to Music or to the video side."""
    data = request.get_json(force=True, silent=True) or {}
    to = data.get("to")
    if to not in ("Music", "Videos"):
        return jsonify(error="Move it to Music or Videos."), 400
    with _tidy_lock:
        if data.get("name"):
            path = _safe_path(data["name"])
            if path is None:
                return jsonify(error="File not found."), 404
            if (_rel(path).split("/")[0] == "Music") == (to == "Music"):
                return jsonify(name=_rel(path))
            name = _to_music_single(path) if to == "Music" else _to_videos(path)
            _forget_gone_folders()
            return jsonify(name=name)

        src = _safe_path(data.get("key") or "", dir_ok=True)
        if src is None:
            return jsonify(error="Collection not found."), 404
        key = _rel(src)
        c = _collection_info(key)
        if c["kind"] not in ("album", "singles", "channel", "playlist"):
            return jsonify(error="That folder can't be moved."), 400
        if (c["section"] == "Music") == (to == "Music"):
            return jsonify(key=key)
        files = _media_in(src)
        if c["kind"] == "album":   # → a playlist folder, in album order
            base = folder = f"Playlists/{_clean(c['title'])}"
            n = 2
            while (DOWNLOAD_DIR / folder).exists():
                folder, n = f"{base} ({n})", n + 1
            for n, p in enumerate(sorted(files, key=lambda p: (_position(p) or 10**6, p.name)), 1):
                _to_videos(p, folder, _position(p) or n)
            _carry_folder(key, folder, c["title"])
            new_key = folder
        elif c["kind"] == "singles":
            new_key = None
            for p in files:
                new_key = Path(_to_videos(p)).parent.as_posix()
        elif c["kind"] == "channel":
            new_key = None
            for p in files:
                new_key = Path(_to_music_single(p)).parent.as_posix()
        else:
            new_key = _playlist_to_album(src, key)
        _prune_empty_dirs(src)
        _forget_gone_folders()
        return jsonify(key=new_key)


# ---------------------------------------------------------------- media

def _file_body(f, length: int):
    """`length` bytes of an open file from its current position. Under gunicorn the
    kernel copies them straight to the socket (sendfile, bounded by Content-Length);
    elsewhere they are streamed in chunks. Either way nothing big sits in memory."""
    if request.environ.get("SERVER_SOFTWARE", "").startswith("gunicorn"):
        return wrap_file(request.environ, f, buffer_size=256 * 1024)

    def chunks():
        left = length
        try:
            while left > 0:
                data = f.read(min(256 * 1024, left))
                if not data:
                    break
                left -= len(data)
                yield data
        finally:
            f.close()
    return chunks()


@app.get("/media/<path:name>")
def media(name: str):
    """Stream a library file with HTTP Range support so seeking works."""
    path = _safe_path(name)
    if path is None:
        return jsonify(error="File not found."), 404
    size = path.stat().st_size
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    if path.suffix.lower() == ".m4a":
        mime = "audio/mp4"

    m = re.match(r"^bytes=(\d*)-(\d*)$", request.headers.get("Range", "").strip())
    start, end, status = 0, size - 1, 200
    if m and (m.group(1) or m.group(2)):
        if m.group(1):                      # bytes=start-  /  bytes=start-end
            start = int(m.group(1))
            end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
        else:                               # suffix range: bytes=-N (last N bytes)
            start = max(size - int(m.group(2)), 0)
        if start >= size or start > end:
            resp = Response(status=416)
            resp.headers["Content-Range"] = f"bytes */{size}"
            return resp
        status = 206
    length = end - start + 1
    f = path.open("rb")
    f.seek(start)
    resp = Response(_file_body(f, length), status, mimetype=mime, direct_passthrough=True)
    resp.headers["Content-Length"] = str(length)
    if status == 206:
        resp.headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["Accept-Ranges"] = "bytes"
    resp.headers["Content-Disposition"] = "inline"
    return resp


@app.get("/thumb/<path:name>")
def thumb(name: str):
    """Thumbnail: YouTube's own if we have it, else embedded art / a poster frame."""
    path = _safe_path(name)
    if path is None:
        return jsonify(error="File not found."), 404
    src = _thumb_image(path, own=bool(request.args.get("own")))
    if src is None:
        return _placeholder(audio=path.suffix.lower() in AUDIO_EXTS)
    return _send_image(src, 31536000 if request.args.get("v") else 86400)


# ---------------------------------------------------------------- UI

@app.get("/")
@app.get("/<path:path>")
def spa(path: str = ""):
    """Serve the built React app; unknown paths fall back to index.html."""
    if path.startswith(("api/", "media/", "thumb/", "playlist-cover/", "collection-cover/")):
        return jsonify(error="Not found."), 404
    if not (WEB_DIST / "index.html").exists():
        return Response(
            "UI is not built yet. Run ./run.sh (or `cd web && bun run build`).",
            503, mimetype="text/plain",
        )
    if path and (WEB_DIST / path).is_file():
        return send_from_directory(WEB_DIST, path, max_age=31536000 if path.startswith("assets/") else 0)
    return send_from_directory(WEB_DIST, "index.html", max_age=0)


# ---------------------------------------------------------------- background threads
# (the tests import the app without them: TUBEGRAB_NO_THREADS=1)
if os.environ.get("TUBEGRAB_NO_THREADS") != "1":
    threading.Thread(target=_indexer.run, daemon=True, name="indexer").start()
    threading.Thread(target=_jobs_publisher, daemon=True, name="jobs-events").start()
    threading.Thread(target=_startup, daemon=True, name="migrate").start()


if __name__ == "__main__":
    app.run(
        host=os.environ.get("TUBEGRAB_HOST", "0.0.0.0"),
        port=int(os.environ.get("TUBEGRAB_PORT", "8765")),
        debug=False,
        threaded=True,
    )
