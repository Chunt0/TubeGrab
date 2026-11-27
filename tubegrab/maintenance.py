"""Housekeeping at startup and after downloads: migrating older layouts,
refiling playlist songs into albums, merging artist/album spellings, and
backfilling captions, chapters and lyrics."""

import shutil
import threading
from pathlib import Path

import yt_dlp

from .config import (
    AUDIO_EXTS, COOKIES_FILE, DOWNLOAD_DIR, FOLDERS_FILE, ID_RE, INDEX_PREFIX_RE, JS_RUNTIMES, META_DIR,
    _meta_lock, SECTIONS, SINGLES, SUB_OPTS, _tidy_lock, VIDEO_EXTS, YEAR_RE,
)
from .meta import (
    _folders, _forget_gone_folders, _ids_by_path, _index_move, _index_set, _iter_media, _kind, _meta_for,
    _note_folder, _prune_empty_dirs, _rel, _retag, _unique, _video_id, _write_json, _write_meta,
)
from .naming import _clean, _fold, _plan, _track_title
from .images import _crop_square, _embed_cover, warm_thumbs
from .lyrics import backfill_lyrics
from .albums import _album_cover, _identify_album
from .genres import backfill_genres
from .index import _indexer
from .jobs import _GrabDL


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
    threading.Thread(target=backfill_genres, daemon=True, name="genres").start()
    warm_thumbs()
