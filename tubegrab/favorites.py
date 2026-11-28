"""Favorites and dislikes: hearted / disliked artists, albums and songs.

The two are one mechanism with two lists, and an item is in at most one of
them: hearting a disliked song takes the dislike away, and the other way round.
Albums keep theirs in their folder record ("fav" / "dislike" in folders.json),
which renames and tidy-up carry along with the rest of the record. Songs are
references like playlist items ("<id>:<kind>", or "path:<rel>"), which survive
re-filing and match the index's ref column. Artists are names.

Dislikes only record what the user doesn't like (for agents to read and act
on); nothing in the app hides or skips them."""

from .config import _meta_lock, DISLIKES_FILE, FAVORITES_FILE, FOLDERS_FILE
from .events import _lib_bump
from .meta import _folders, _read_json, _write_json
from .files import _safe_path
from .playlists import _pl_ref

KINDS = ("artist", "album", "song")
MARKS = {"fav": FAVORITES_FILE, "dislike": DISLIKES_FILE}   # mark → its file; also the folder record's flag


def _lists(mark: str) -> dict:
    data = _read_json(MARKS[mark], {})
    return {"artists": list(data.get("artists") or []), "songs": list(data.get("songs") or [])}


def marked(mark: str) -> dict:
    """{"artists": [name], "albums": [collection key], "songs": [ref]}"""
    out = _lists(mark)
    out["albums"] = sorted(k for k, v in _folders().items() if v.get(mark))
    return out


def set_mark(mark: str, kind: str, ident: str, on: bool) -> str | None:
    """Mark (or unmark) one artist / album / song; an error message if it can't.
    Marking clears the other mark on the same item."""
    if kind not in KINDS or not isinstance(ident, str) or not ident.strip() or len(ident) > 500:
        return "Bad item."
    others = [m for m in MARKS if m != mark] if on else []
    with _meta_lock:
        if kind == "album":
            if _safe_path(ident, dir_ok=True) is None or not ident.startswith("Music/"):
                return "Album not found."
            folders = _folders()
            entry = folders.setdefault(ident, {})
            if on:
                entry[mark] = True
            else:
                entry.pop(mark, None)
            for m in others:
                entry.pop(m, None)
            if not entry:
                folders.pop(ident)
            _write_json(FOLDERS_FILE, folders)
        else:
            if kind == "song":
                path = _safe_path(ident)
                if path is None:
                    return "Song not found."
                key, item = "songs", _pl_ref(path)
            else:
                key, item = "artists", ident.strip()
            for m in [mark, *others]:
                lists = _lists(m)
                if item not in lists[key] and not (m == mark and on):
                    continue
                rest = [x for x in lists[key] if x != item]
                lists[key] = [item, *rest] if m == mark and on else rest   # newest first
                _write_json(MARKS[m], lists)
    _lib_bump()
    return None


def favorites() -> dict:
    return marked("fav")


def dislikes() -> dict:
    return marked("dislike")


def set_favorite(kind: str, ident: str, on: bool) -> str | None:
    return set_mark("fav", kind, ident, on)


def set_dislike(kind: str, ident: str, on: bool) -> str | None:
    return set_mark("dislike", kind, ident, on)


def favorite_refs() -> list[str]:
    return _lists("fav")["songs"]
