"""Moving files and whole collections between Music and the video side.

Audio downloads land in Music; the odd lecture grabbed as audio can be moved
to the video side (and a video, or a whole collection, the other way). Files
you moved are marked "placed" in their metadata so tidying never moves them back."""

import shutil
from pathlib import Path

from .config import AUDIO_EXTS, DOWNLOAD_DIR, FOLDERS_FILE, INDEX_PREFIX_RE, MEDIA_EXTS, _meta_lock, SINGLES
from .meta import (
    _folders, _index_move, _meta_for, _prune_empty_dirs, _read_json, _rel, _retag, _unique, _video_id,
    _write_json, _write_meta,
)
from .naming import _album_folder, _artist_dir, _clean, _clean_channel, _local_title, _track_title
from .files import _display_title
from .lyrics import _fetch_lyrics_bg
from .albums import _album_cover, ALBUMS_FILE, _albums_lock, _guess_album, _identify_album



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
