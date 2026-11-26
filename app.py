#!/usr/bin/env python3
"""
TubeGrab — a tiny local web UI around yt-dlp.

Paste a YouTube video or playlist URL (your own uploads or content you are
licensed to download), pick a quality, and the file is filed into the library
automatically:

    Music/<Artist>/<Album (Year)>/<NN - Track>.m4a     audio (album or single)
    Music/<Artist>/Singles/<Track>.m4a                 audio without album info
    Videos/<Channel>/<Title>.mp4                       single videos
    Playlists/<Playlist>/<NNN - Title>.mp4             any other playlist
    .meta/                                             index, metadata, thumbnails

Run:  ./run.sh   →  http://127.0.0.1:8765
"""

import hashlib
import json
import mimetypes
import os
import re
import shutil
import subprocess
import threading
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
for _d in (DOWNLOAD_DIR, META_DIR, THUMBS_DIR, PL_COVERS_DIR, COVERS_DIR):
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


def _plan(info: dict, *, audio: bool, playlist_mode: bool) -> dict:
    """Decide where an entry goes. Returns {rel (no extension), section, tags}."""
    title = info.get("track") or info.get("title") or info.get("id") or "Untitled"
    in_playlist = playlist_mode and bool(info.get("playlist_id"))
    album_pl = in_playlist and _is_album_playlist(info)
    artist = _artist(info)
    tags: dict = {}

    has_album = bool(info.get("album") and artist)
    # a regular playlist that is really one album ("The Smashing Pumpkins: Gish (Deluxe Edition)")
    album_like = album_pl or (in_playlist and has_album and len(_fold(info["album"])) >= 3
                              and _fold(info["album"]) in _fold(info.get("playlist_title") or ""))
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

    if in_playlist:
        pl = info.get("playlist_title") or "Playlist"
        idx = info.get("playlist_index")
        name = f"{int(idx):03d} - {_clean(title)}" if idx else _clean(title)
        return {"rel": f"Playlists/{_clean(pl)}/{name}", "section": "Playlists", "tags": tags,
                "collection_title": pl, "collection_url": info.get("playlist_webpage_url")}

    if audio:
        channel = _clean_channel(info.get("uploader") or info.get("channel"))
        return {"rel": f"Music/{_clean(channel)}/{SINGLES}/{_clean(title)}", "section": "Music",
                "tags": {"artist": channel, "album_artist": channel}, "collection_title": None,
                "collection_url": None}

    channel = _clean_channel(info.get("uploader") or info.get("channel"))
    return {"rel": f"Videos/{_clean(channel)}/{_clean(title)}", "section": "Videos", "tags": tags,
            "collection_title": channel, "collection_url": info.get("channel_url") or info.get("uploader_url")}


# ---------------------------------------------------------------- metadata store

def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text("utf-8"))
    except Exception:  # noqa: BLE001
        return default


def _write_json(path: Path, data) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
    tmp.replace(path)


def _rel(path: Path) -> str:
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


def _iter_media():
    """All library media files (skips .meta, hidden dirs and yt-dlp temp files)."""
    for path in DOWNLOAD_DIR.rglob("*"):
        rel = path.relative_to(DOWNLOAD_DIR)
        if any(part.startswith(".") for part in rel.parts):
            continue
        # skip pre-merge streams ("x.f399.mp4") and merge temp files ("x.temp.mp4")
        if re.search(r"\.(f\d+|temp)$", path.stem):
            continue
        if path.is_file() and path.suffix.lower() in MEDIA_EXTS:
            yield path


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


def _add_file(job_id: str, path: str) -> None:
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

    def __init__(self, params, *, job_id: str, playlist_mode: bool, kind: str):
        super().__init__(params)
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
        plan = _plan(info_dict, audio=audio, playlist_mode=self.playlist_mode)
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
        if existing == dest or dest.exists():
            return existing
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


def _run_job(job_id: str, url: str, fmt: str, playlist: bool) -> None:
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
        with _GrabDL(opts, job_id=job_id, playlist_mode=playlist,
                     kind="audio" if fmt == "audio" else "video") as ydl:
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
    finally:
        _tidy_when_idle()


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
_probe_dirty = False


def _save_probe_cache() -> None:
    global _probe_dirty
    if not _probe_dirty:
        return
    _probe_dirty = False
    with _meta_lock:
        live = {k: v for k, v in _probe_cache.items() if Path(k.split("|")[0]).exists()}
        _write_json(PROBE_FILE, live)


def _probe(path: Path) -> dict:
    """Duration + video height via ffprobe, cached per path+mtime."""
    st = path.stat()
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
    global _probe_dirty
    _probe_cache[key] = res
    _probe_dirty = True
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


def _progress_fields(p: dict | None) -> dict:
    return {"pos": (p or {}).get("pos") or 0, "watched": bool((p or {}).get("watched"))}


def _clean_chapters(meta: dict) -> list[dict] | None:
    """Real chapters only (not the single 'whole video' one cutting sponsors leaves)."""
    ch = meta.get("chapters") or []
    if len(ch) < 2 or all(c.get("title") == meta.get("title") for c in ch):
        return None
    return [{"start": round(c.get("start_time") or 0, 2), "title": c.get("title") or ""} for c in ch]


def _library() -> tuple[list[dict], list[dict]]:
    folders = _folders()
    by_path = _ids_by_path()
    prog = _progress()
    names = os.listdir(META_DIR)
    subs = {n.split(".")[0] for n in names if n.endswith(".vtt")}
    lyrics = {n[:-4]: "synced" for n in names if n.endswith(".lrc")} | {n[:-11]: "plain" for n in names if n.endswith(".lyrics.txt")}
    files: list[dict] = []
    for path in _iter_media():
        rel = _rel(path)
        st = path.stat()
        vid = _video_id(path, by_path)
        meta = _meta_for(vid)
        probe = _probe(path)
        key = Path(rel).parent.as_posix()
        key = "" if key == "." else key
        c = _collection_info(key) if key else {"section": "Other", "artist": None}
        m = INDEX_PREFIX_RE.match(path.name)
        files.append({
            "name": rel,
            "title": meta.get("track") or meta.get("title") or _display_title(path.name),
            "kind": _kind(path),
            "size": st.st_size,
            "mtime": st.st_mtime,
            "duration": probe["duration"] or meta.get("duration"),
            "height": probe["height"] if _kind(path) == "video" else None,
            "section": c["section"],
            "collection": key,
            "id": vid,
            "artist": meta.get("artist") or c.get("artist") or _clean_channel(meta.get("uploader")),
            "album": meta.get("album"),
            "uploader": _clean_channel(meta.get("uploader") or meta.get("channel")),
            "upload_date": meta.get("upload_date"),
            "year": meta.get("release_year"),
            "url": meta.get("webpage_url"),
            "index": int(m.group(1)) if m else None,
            "art": (_chosen_cover(path, folders) or (None, None))[1] if c["section"] == "Music" else None,
            **_progress_fields(prog.get(f"{vid}:{_kind(path)}" if vid else f"path:{rel}")),
            "chapters": _clean_chapters(meta),
            "subs": bool(vid and vid in subs),
            "lyrics": lyrics.get(vid) if vid and _kind(path) == "audio" else None,
        })
    files.sort(key=lambda f: f["mtime"], reverse=True)
    _save_probe_cache()

    collections: dict[str, dict] = {}
    for f in files:
        if not f["collection"]:
            continue
        c = collections.get(f["collection"])
        if c is None:
            c = collections[f["collection"]] = _collection_info(f["collection"])
            extra = folders.get(f["collection"], {})
            if extra.get("title") and c["kind"] != "singles":
                c["title"] = extra["title"]   # YouTube's original name, or the user's edit
                c["named"] = True
            c.update(url=extra.get("url"), count=0, size=0, duration=0.0, updated=0.0, covers=[])
        c["count"] += 1
        c["size"] += f["size"]
        c["duration"] += f["duration"] or 0
        c["updated"] = max(c["updated"], f["mtime"])
    for c in collections.values():
        members = [f for f in files if f["collection"] == c["key"]]
        if c["kind"] in ("album", "playlist"):
            members.sort(key=lambda f: (f["index"] is None, f["index"] or 0, f["title"].lower()))
        custom = _folder_cover(c["key"], folders)
        if custom:
            c["art"] = custom[1]
            if custom[0].parent == COVERS_DIR:
                c["cover_url"] = f"/collection-cover/{custom[1]}"
            else:   # an item's thumbnail: put that item first
                members.sort(key=lambda f: f["id"] != custom[1])   # stable: rest keep their order
        c["covers"] = [f["name"] for f in members[:3]]
        c.pop("named", None) if c["kind"] != "album" else None
        if c["kind"] == "album" and not c.pop("named", False):
            # real album name from tags ("Global Entry: Deluxe"), not the filename-safe folder
            albums = [f["album"] for f in members if f.get("album")]
            if albums:
                c["title"] = max(set(albums), key=albums.count)
    return files, sorted(collections.values(), key=lambda c: c["updated"], reverse=True)


def _placeholder(audio: bool = False):
    return send_file(PLACEHOLDER_AUDIO if audio else PLACEHOLDER, mimetype="image/svg+xml", max_age=3600)


def _embedded_art(path: Path):
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
    return send_file(out, mimetype="image/jpeg", max_age=86400) if out.exists() and out.stat().st_size else None


# ---------------------------------------------------------------- migration

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
    for path in sorted(root.glob("*/*")):
        if not path.is_file() or path.suffix.lower() not in AUDIO_EXTS:
            continue
        vid = _video_id(path, by_path)
        meta = _meta_for(vid)
        if not vid or not meta.get("album") or not meta.get("playlist_id"):
            continue
        plan = _plan(dict(meta), audio=True, playlist_mode=True)
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
    if moved:
        _forget_gone_folders()
        print(f"[tubegrab] moved {moved} song(s) from Playlists/ into Music/")


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


threading.Thread(target=_startup, daemon=True, name="migrate").start()


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


@app.get("/api/files")
def api_files():
    files, collections = _library()
    return jsonify(dir=str(DOWNLOAD_DIR), files=files, collections=collections, playlists=_playlists_out())


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


def _parse_vtt(text: str) -> list[dict]:
    """Cues → [{start, end, text}], collapsing YouTube's rolling auto-captions
    (each cue repeats the previous line) into one entry per spoken line."""
    import html
    segs: list[dict] = []
    last: list[str] = []
    for block in re.split(r"\n\s*\n", text.replace("\r", "")):
        lines = block.strip().split("\n")
        i = next((k for k, l in enumerate(lines) if "-->" in l), None)
        if i is None:
            continue
        a, b = lines[i].split("-->")[:2]
        start, end = _ts(a), _ts(b.split()[0] if b.split() else b)
        if end - start < 0.05:          # zero-length "transition" cues
            continue
        body = [re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", l))).strip() for l in lines[i + 1:]]
        body = [l for l in body if l]
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
    return segs


@app.get("/api/transcript/<path:name>")
def api_transcript(name: str):
    path = _safe_path(name)
    if path is None:
        return jsonify(error="File not found."), 404
    return jsonify(segments=_transcript(_video_id(path)))


@app.get("/api/search")
def api_search():
    """Find words spoken in your videos (their captions)."""
    q = re.sub(r"\s+", " ", (request.args.get("q") or "")).strip().casefold()
    if len(q) < 3:
        return jsonify(results=[])
    by_path = _ids_by_path()
    results = []
    for path in _iter_media():
        if path.suffix.lower() not in VIDEO_EXTS:
            continue
        segs = _transcript(_video_id(path, by_path))
        hits, last_hit = [], -10
        for k, sg in enumerate(segs):
            window = (sg["text"] + " " + (segs[k + 1]["text"] if k + 1 < len(segs) else "")).casefold()
            if q in window and k > last_hit + 1:
                hits.append({"t": sg["start"], "text": sg["text"] + (" " + segs[k + 1]["text"] if k + 1 < len(segs) and q not in sg["text"].casefold() else "")})
                last_hit = k
        if hits:
            results.append({"name": _rel(path), "count": len(hits), "hits": hits[:3]})
    results.sort(key=lambda r: -r["count"])
    return jsonify(results=results[:30])


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


def _pl_out(pid: str, p: dict, idx: dict) -> dict:
    items = [r for r in (_pl_resolve(ref, idx) for ref in p.get("items", [])) if r]
    cover = p.get("cover") or {}
    return {
        "id": pid, "title": p.get("title") or "Untitled", "description": p.get("description") or "",
        "items": items, "created": p.get("created"), "updated": p.get("updated"),
        "cover_item": _pl_resolve(cover["item"], idx) if cover.get("item") else None,
        "cover_upload": cover.get("upload"),   # version string → /playlist-cover/<id>?v=
    }


def _playlists_out() -> list[dict]:
    idx = _index()
    out = [_pl_out(pid, p, idx) for pid, p in _pl_all().items()]
    return sorted(out, key=lambda p: p["updated"] or "", reverse=True)


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
    return send_file(path, mimetype="image/jpeg", max_age=86400)


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
    return send_file(COVERS_DIR / name, mimetype="image/jpeg", max_age=31536000)   # names are unique per upload


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


# ---------------------------------------------------------------- media

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
    if m and (m.group(1) or m.group(2)):
        if m.group(1):                      # bytes=start-  /  bytes=start-end
            start = int(m.group(1))
            end = int(m.group(2)) if m.group(2) else min(start + 8 * 1024 * 1024, size - 1)
        else:                               # suffix range: bytes=-N (last N bytes)
            start = max(size - int(m.group(2)), 0)
            end = size - 1
        end = min(end, size - 1)
        if start >= size or start > end:
            resp = Response(status=416)
            resp.headers["Content-Range"] = f"bytes */{size}"
            return resp
        with path.open("rb") as f:
            f.seek(start)
            data = f.read(end - start + 1)
        resp = Response(data, 206, mimetype=mime)
        resp.headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    else:
        # full file: stream through in chunks without loading it into memory
        resp = Response(wrap_file(request.environ, path.open("rb")),
                        mimetype=mime, direct_passthrough=True)
        resp.headers["Content-Length"] = str(size)
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
    chosen = None if request.args.get("own") else _chosen_cover(path)
    yt = chosen[0] if chosen else _meta_thumb(_video_id(path))
    if yt:
        return send_file(yt, max_age=300)
    if path.suffix.lower() in AUDIO_EXTS:
        return _embedded_art(path) or _placeholder(audio=True)
    st = path.stat()
    key = hashlib.sha1(f"{name}|{int(st.st_mtime)}".encode()).hexdigest()[:16]
    out = THUMBS_DIR / f"{key}.jpg"
    if not out.exists():
        _generate_thumb(path, out)
    if out.exists():
        return send_file(out, mimetype="image/jpeg", max_age=86400)
    return _placeholder()


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


if __name__ == "__main__":
    app.run(
        host=os.environ.get("TUBEGRAB_HOST", "0.0.0.0"),
        port=int(os.environ.get("TUBEGRAB_PORT", "8765")),
        debug=False,
        threaded=True,
    )
