"""The .meta store: index.json (video id → library path), folders.json
(collection titles, links, covers), per-video metadata, progress, and the
small file operations (retag, unique names, pruning) built on them."""

import json
import os
import re
from pathlib import Path

from .config import (
    AUDIO_EXTS, COVERS_DIR, DOWNLOAD_DIR, FOLDERS_FILE, ID_RE, INDEX_FILE, MEDIA_EXTS, META_DIR, _meta_lock,
    _now, PLAYLISTS_FILE, PROGRESS_FILE, THUMB_EXTS,
)
from .events import _lib_bump


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text("utf-8"))
    except Exception:  # noqa: BLE001
        return default


def _meta_changed(**changes) -> None:
    """Tell the library index something changed (it looks for changed folders, plus
    any rows / ids / folders named)."""
    from .index import _indexer   # (deferred: the index builds on this module)
    _indexer.kick(**changes)


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


# Where you stopped in each video (and whether you finished it), shared by
# every device that opens the library.

def _progress() -> dict:
    return _read_json(PROGRESS_FILE, {})
