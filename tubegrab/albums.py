"""Recognising a playlist of fan uploads as one album.

Someone's "Primus - Frizzle Fry (full album)" playlist of fan uploads carries no
music metadata, so _plan would file it as a plain playlist. Before an audio
playlist downloads we look at its title and entry titles; when it looks like
one album, MusicBrainz (free, open music database; only the guessed artist and
album name are sent) confirms it and gives the real track list, numbers, year
and cover. Answers are kept per playlist in .meta/albums.json."""

import json
import re
import threading
import time
import uuid
from pathlib import Path

import yt_dlp

from .config import AUDIO_EXTS, COVERS_DIR, DOWNLOAD_DIR, FOLDERS_FILE, META_DIR, _meta_lock
from .meta import _folders, _read_json, _write_json
from .naming import _is_album_playlist, _local_title, _norm, _SEP
from .images import _embed_cover, _save_picture



ALBUMS_FILE = META_DIR / "albums.json"
MUSICBRAINZ = "https://musicbrainz.org/ws/2"
COVER_ART = "https://coverartarchive.org"
_ALBUM_WORDS = re.compile(
    r"[\(\[]?\s*\b(?:(?:the\s+)?(?:full|complete|whole|entire)\s+(?:album|lp|ep|record)|album\s+(?:completo|complet|completa)"
    r"|full\s*-\s*album|fullalbum)\b\s*[\)\]]?", re.I)
_PL_JUNK = re.compile(r"[\(\[]\s*(?:hq|hd|high quality|remaster(?:ed)?|\d{4}|lyrics?|audio)\s*[\)\]]", re.I)
_mb_last = 0.0
_mb_lock = threading.Lock()
_albums_lock = threading.Lock()


def _http_json(url: str, *, mb: bool = False):
    import urllib.error
    import urllib.request
    global _mb_last
    req = urllib.request.Request(url, headers={"User-Agent": "TubeGrab/1.0 (self-hosted music library)",
                                               "Accept": "application/json"})
    for attempt in range(3):
        if mb:
            with _mb_lock:   # (two downloads may look albums up at once)
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
