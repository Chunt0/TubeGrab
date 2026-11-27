"""Genres for the music library, from MusicBrainz (free, open music database;
only artist and album names are sent).

An album gets its release group's genres, or its artist's when the album has
none; a Singles folder gets the artist's. They're kept in the folder record
("genres" in folders.json, [] once asked and none found), so each is asked
once and survives renames. Looked up in the background: after startup and
after downloads, at MusicBrainz's pace (one request a second)."""

import json
import threading
import urllib.parse

from .config import DOWNLOAD_DIR, FOLDERS_FILE, _meta_lock
from .meta import _folders, _write_json
from .db import _db
from .albums import MUSICBRAINZ, _http_json, _mb_quote

MAX_GENRES = 5
_running = threading.Lock()


def _top(entity: dict | None) -> list[str]:
    """The most-voted genres of a MusicBrainz artist or release group."""
    voted = [g for g in (entity or {}).get("genres") or [] if (g.get("count") or 0) > 0 and g.get("name")]
    return [g["name"] for g in sorted(voted, key=lambda g: -g["count"])[:MAX_GENRES]]


def _search(entity: str, query: str, min_score: int) -> dict | None:
    params = urllib.parse.urlencode({"query": query, "limit": 3, "fmt": "json"})
    found = _http_json(f"{MUSICBRAINZ}/{entity}/?{params}", mb=True) or {}
    return next((x for x in found.get(f"{entity}s", []) if (x.get("score") or 0) >= min_score), None)


def album_genres(artist: str, album: str) -> list[str]:
    rg = _search("release-group", f"releasegroup:{_mb_quote(album)} AND artist:{_mb_quote(artist)}", 90)
    return _top(_http_json(f"{MUSICBRAINZ}/release-group/{rg['id']}?inc=genres&fmt=json", mb=True)) if rg else []


def artist_genres(artist: str) -> list[str]:
    a = _search("artist", f"artist:{_mb_quote(artist)}", 95)
    return _top(_http_json(f"{MUSICBRAINZ}/artist/{a['id']}?inc=genres&fmt=json", mb=True)) if a else []


def backfill_genres() -> None:
    """Look up genres for music folders that haven't been asked about yet. Stops
    (to try again next time) when MusicBrainz can't be reached."""
    if not _running.acquire(blocking=False):
        return
    try:
        asked = {k for k, v in _folders().items() if "genres" in v}
        rows = _db().execute("SELECT key, data FROM collections WHERE section='Music'").fetchall()
        by_artist: dict[str, list[str]] = {}
        for key, data in rows:
            c = json.loads(data)
            if key in asked or c.get("kind") not in ("album", "singles") or not c.get("artist"):
                continue
            try:
                genres = album_genres(c["artist"], c["title"]) if c["kind"] == "album" else []
                if not genres:
                    if c["artist"] not in by_artist:
                        by_artist[c["artist"]] = artist_genres(c["artist"])
                    genres = by_artist[c["artist"]]
            except Exception as exc:  # noqa: BLE001 — offline: try again later
                print(f"[tubegrab] genre lookup paused: {exc}")
                return
            with _meta_lock:
                if not (DOWNLOAD_DIR / key).is_dir():
                    continue
                folders = _folders()
                folders.setdefault(key, {})["genres"] = genres
                _write_json(FOLDERS_FILE, folders)
    finally:
        _running.release()
