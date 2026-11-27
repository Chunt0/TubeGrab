"""Editing routes: delete files and collections, covers, collection details,
and moving between Music and Videos."""

import re
import shutil
import uuid
from pathlib import Path

from flask import Blueprint, jsonify, request

from ..config import (
    AUDIO_EXTS, COVERS_DIR, DOWNLOAD_DIR, FOLDERS_FILE, _jobs, _lock, MEDIA_EXTS, META_DIR, _meta_lock,
    SINGLES, _tidy_lock,
)
from ..meta import (
    _folders, _forget_gone_folders, _index, _index_drop, _index_move, _meta_thumb, _prune_empty_dirs, _rel,
    _retag, _video_id, _write_json, _write_meta,
)
from ..naming import _clean
from ..files import _collection_info, _safe_path
from ..images import BAD_PICTURE, _embed_cover, _save_picture, _send_image
from ..playlists import _playlists_drop_missing
from ..moves import _carry_folder, _media_in, _playlist_to_album, _position, _to_music_single, _to_videos


bp = Blueprint("collections", __name__)


@bp.post("/api/files/delete")
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


@bp.post("/api/collections/delete")
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


@bp.post("/api/collections/cover")
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


@bp.post("/api/collections/cover/upload")
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


@bp.get("/collection-cover/<name>")
def collection_cover(name: str):
    if not re.fullmatch(r"[0-9a-f]{10}\.jpg", name) or not (COVERS_DIR / name).is_file():
        return jsonify(error="Not found."), 404
    return _send_image(COVERS_DIR / name, 31536000, "image/jpeg")   # names are unique per upload


@bp.post("/api/collections/edit")
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


@bp.post("/api/move")
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
