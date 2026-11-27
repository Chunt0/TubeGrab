"""Favorites: hearted artists, albums and songs.

Albums keep theirs in their folder record ("fav" in folders.json), which
renames and tidy-up carry along with the rest of the record. Songs are references like
playlist items ("<id>:<kind>", or "path:<rel>"), which survive re-filing and
match the index's ref column. Artists are names."""

from .config import _meta_lock, FAVORITES_FILE, FOLDERS_FILE
from .events import _lib_bump
from .meta import _folders, _read_json, _write_json
from .files import _safe_path
from .playlists import _pl_ref

KINDS = ("artist", "album", "song")


def _favs() -> dict:
    data = _read_json(FAVORITES_FILE, {})
    return {"artists": list(data.get("artists") or []), "songs": list(data.get("songs") or [])}


def favorites() -> dict:
    """{"artists": [name], "albums": [collection key], "songs": [ref]}"""
    out = _favs()
    out["albums"] = sorted(k for k, v in _folders().items() if v.get("fav"))
    return out


def set_favorite(kind: str, ident: str, on: bool) -> str | None:
    """Heart (or un-heart) one artist / album / song; an error message if it can't."""
    if kind not in KINDS or not isinstance(ident, str) or not ident.strip() or len(ident) > 500:
        return "Bad favorite."
    with _meta_lock:
        if kind == "album":
            if _safe_path(ident, dir_ok=True) is None or not ident.startswith("Music/"):
                return "Album not found."
            folders = _folders()
            entry = folders.setdefault(ident, {})
            if on:
                entry["fav"] = True
            else:
                entry.pop("fav", None)
                if not entry:
                    folders.pop(ident)
            _write_json(FOLDERS_FILE, folders)
            return None
        favs = _favs()
        if kind == "song":
            path = _safe_path(ident)
            if path is None:
                return "Song not found."
            key, item = "songs", _pl_ref(path)
        else:
            key, item = "artists", ident.strip()
        items = [x for x in favs[key] if x != item]
        favs[key] = [item, *items] if on else items   # newest first
        _write_json(FAVORITES_FILE, favs)
    _lib_bump()
    return None


def favorite_refs() -> list[str]:
    return _favs()["songs"]
